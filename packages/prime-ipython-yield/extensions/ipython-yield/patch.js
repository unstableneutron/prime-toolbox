/**
 * Runtime patch that caps how long a single IPython cell may hold the agent's
 * only tool.
 *
 * Prime Agent 0.7 exposes `ipython` as the sole model-facing tool, backed by one
 * single-threaded kernel. `KernelManager.execute()` resolves only when the cell
 * reaches iopub `status: idle`, so a blocking cell pins the whole agent for its
 * full duration. There is no extension hook for cell scheduling, so this package
 * patches two narrow, public methods and fails loudly if their shape changes.
 *
 * What the patch does NOT do: it never interrupts or cancels the underlying
 * cell. The cell keeps running exactly as before; only the host's *wait* is
 * capped. Recovering the result is the caller's job (see ipython_attach).
 */

import { busyResult, clampInt, detachedResult } from "./registry.js";
import { DetachedCellRegistry } from "./registry.js";

export const PATCH_STATE = Symbol.for("prime-toolbox:ipython-yield");

const ENSURE_METHOD = "ensure";
const EXECUTE_METHOD = "execute";

export const DEFAULT_YIELD_MS = 10_000;
export const MIN_YIELD_MS = 1_000;
export const MAX_YIELD_MS = 300_000;
const YIELD = Symbol("yield");

/**
 * Shared state threaded through both patches.
 *
 * `readyManagers` is the guard that keeps startup traffic untouched: a manager
 * only becomes eligible for yielding after `ensure()` has fully resolved for it,
 * which is after the kernel bootstrap cell has run. Bootstrap is a normal
 * (non-internal) execute, so without this guard a slow boot would "detach" the
 * runtime bootstrap and leave the kernel unusable.
 */
export function createYieldRuntime(options = {}) {
  const registry = options.registry ?? new DetachedCellRegistry();
  const readyManagers = new WeakSet();
  return {
    registry,
    readyManagers,
    onError: options.onError ?? (() => {}),
    yieldMs() {
      const raw = typeof options.yieldMs === "function" ? options.yieldMs() : options.yieldMs;
      if (raw === 0) return 0; // explicit opt-out: restore stock blocking behaviour
      // minYieldMs is a test seam; production callers must not lower the floor,
      // or a slow-but-normal cell would detach before it had a chance to finish.
      const floor = clampInt(options.minYieldMs, 1, MIN_YIELD_MS, MIN_YIELD_MS);
      return clampInt(raw, floor, MAX_YIELD_MS, Math.max(floor, DEFAULT_YIELD_MS));
    },
    cancelWaitMs() {
      // Test seam only; production uses the kernel's own abort grace period.
      return clampInt(options.cancelWaitMs, 1, 30_000, 5_000);
    },
    maxChars() {
      const raw = typeof options.maxChars === "function" ? options.maxChars() : options.maxChars;
      return clampInt(raw, 256, 200_000, 16_000);
    },
    markReady(manager) {
      if (manager && typeof manager === "object") readyManagers.add(manager);
    },
    isReady(manager) {
      return Boolean(manager) && readyManagers.has(manager);
    },
  };
}

function assertPatchable(prototype, method, what) {
  if (!prototype || typeof prototype !== "object") {
    throw new TypeError(`Expected a ${what} prototype object`);
  }
  const descriptor = Object.getOwnPropertyDescriptor(prototype, method);
  if (typeof descriptor?.value !== "function") {
    throw new Error(`Unsupported Prime Agent version: ${what} no longer exposes ${method}()`);
  }
  return descriptor;
}

function markInstalled(prototype, method, descriptor) {
  const existing = prototype[PATCH_STATE];
  const state = existing ? { ...existing } : {};
  state[method] = descriptor;
  Object.defineProperty(prototype, PATCH_STATE, { configurable: true, value: state });
}

/**
 * Patch `KernelManager.prototype.execute`.
 *
 * @param {object} prototype KernelManager.prototype
 * @param {ReturnType<typeof createYieldRuntime>} runtime
 * @returns {"installed" | "already-installed"}
 */
export function installKernelExecuteYield(prototype, runtime) {
  if (prototype?.[PATCH_STATE]?.[EXECUTE_METHOD]) return "already-installed";
  const descriptor = assertPatchable(prototype, EXECUTE_METHOD, "KernelManager");
  const original = descriptor.value;
  const { registry } = runtime;

  async function patchedExecute(code, opts = {}) {
    const options = opts ?? {};

    // Startup, snapshot/restore and namespace listing must behave exactly as
    // before -- they are host bookkeeping, not model-issued work.
    if (options.internal || !runtime.isReady(this)) {
      return original.call(this, code, options);
    }

    // A detached cell still owns the kernel. Do not enqueue behind it: the
    // execution queue would block this call for the detached cell's full
    // remaining lifetime, which is the exact failure this package removes.
    const active = registry.active();
    if (active) return busyResult(active, runtime.maxChars());

    const yieldMs = runtime.yieldMs();
    if (!(yieldMs > 0)) return original.call(this, code, options);

    const cell = registry.create(code);

    // Give the cell its own abort controller. Until we detach it mirrors the
    // tool call's signal (so Ctrl+C still works); once detached we stop
    // forwarding, so a later abort of the finished tool call cannot kill a cell
    // the model deliberately left running. ipython_cancel aborts it directly.
    const controller = new AbortController();
    cell.controller = controller;
    let forwardAbort;
    if (options.signal) {
      if (options.signal.aborted) controller.abort();
      else {
        forwardAbort = () => controller.abort();
        options.signal.addEventListener("abort", forwardAbort, { once: true });
      }
    }
    const stopForwarding = () => {
      if (forwardAbort && options.signal) options.signal.removeEventListener("abort", forwardAbort);
      forwardAbort = undefined;
    };

    const userOnStream = options.onStream;
    const inner = original.call(this, code, {
      ...options,
      signal: controller.signal,
      onStream(chunk, name) {
        cell.append(chunk, name);
        userOnStream?.(chunk, name);
      },
    });
    cell.inner = inner;

    const settled = inner.then(
      (result) => {
        registry.settle(cell, { result });
        return result;
      },
      (error) => {
        registry.settle(cell, { error });
        throw error;
      },
    );
    // A detached cell's rejection is delivered through ipython_attach, not as an
    // unhandled rejection that would take down the worker.
    settled.catch(() => {});

    let timer;
    const yielded = new Promise((resolve) => {
      timer = setTimeout(() => resolve(YIELD), yieldMs);
      if (timer && typeof timer === "object" && "unref" in timer) timer.unref();
    });

    const winner = await Promise.race([settled.then((r) => ({ result: r }), (e) => ({ error: e })), yielded]);
    clearTimeout(timer);
    stopForwarding();

    if (winner !== YIELD) {
      if (winner.error) throw winner.error;
      return winner.result;
    }
    // The cell may have settled inside the race window; prefer the real result.
    if (cell.state !== "running") return settled;

    registry.markDetached(cell);
    return detachedResult(cell, runtime.maxChars());
  }

  Object.defineProperty(prototype, EXECUTE_METHOD, { ...descriptor, value: patchedExecute });
  markInstalled(prototype, EXECUTE_METHOD, descriptor);
  return "installed";
}

/**
 * Patch `IpythonKernelProvisioner.prototype.ensure`.
 *
 * This is the reachability hop: KernelManager is not exported from the package
 * index, but every manager instance flows through ensure(), and patching a
 * prototype reached from an instance intercepts all later calls.
 *
 * @param {object} prototype IpythonKernelProvisioner.prototype
 * @param {ReturnType<typeof createYieldRuntime>} runtime
 * @returns {"installed" | "already-installed"}
 */
export function installIpythonYield(prototype, runtime) {
  if (prototype?.[PATCH_STATE]?.[ENSURE_METHOD]) return "already-installed";
  const descriptor = assertPatchable(prototype, ENSURE_METHOD, "IpythonKernelProvisioner");
  const original = descriptor.value;

  async function patchedEnsure(...args) {
    const manager = await original.apply(this, args);
    if (manager && typeof manager === "object") {
      try {
        installKernelExecuteYield(Object.getPrototypeOf(manager), runtime);
      } catch (error) {
        // A shape change must degrade to stock behaviour, not break the kernel.
        runtime.onError(error);
      }
      runtime.markReady(manager);
    }
    return manager;
  }

  Object.defineProperty(prototype, ENSURE_METHOD, { ...descriptor, value: patchedEnsure });
  markInstalled(prototype, ENSURE_METHOD, descriptor);
  return "installed";
}

/** Restore a prototype patched by this module. Intended for tests. */
export function restoreIpythonYield(prototype) {
  const state = prototype?.[PATCH_STATE];
  if (!state) return false;
  for (const [method, descriptor] of Object.entries(state)) {
    Object.defineProperty(prototype, method, descriptor);
  }
  delete prototype[PATCH_STATE];
  return true;
}
