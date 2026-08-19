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

/**
 * Worker-level routing tables.
 *
 * A daemon worker hosts SEVERAL sessions, each activating this extension with
 * its own runtime (own registry, own tools, own wake listener). Both patches
 * below install on shared prototypes and are idempotent, so the naive shape --
 * closing over the runtime that happened to install first -- silently binds
 * every later session to session #1's registry. That produced three bugs:
 * a sibling's detached cell reported the kernel busy while this session's
 * kernel was idle; this session's ipython_attach/ipython_cancel looked in an
 * empty registry; and the completion wake was delivered to the wrong session.
 *
 * So the patches resolve their runtime from `this` at CALL time instead.
 * Provisioners are constructed per session with `options.sessionId`, which is
 * the routing key; managers are then pinned to the runtime that provisioned
 * them.
 */
const runtimesBySession = new Map();
const runtimesByProvisioner = new WeakMap();
const runtimesByProvisionerPrototype = new WeakMap();
const runtimesByManager = new WeakMap();
/** Last resort for a host that exposes no session id: preserves single-session behaviour. */
let fallbackRuntime;

function sessionIdOf(provisioner) {
  if (!provisioner || typeof provisioner !== "object") return undefined;
  const direct = provisioner.sessionId;
  if (typeof direct === "string" && direct) return direct;
  const viaOptions = provisioner.options?.sessionId;
  return typeof viaOptions === "string" && viaOptions ? viaOptions : undefined;
}

function registerRuntime(runtime, sessionId) {
  if (!fallbackRuntime) fallbackRuntime = runtime;
  if (sessionId) runtimesBySession.set(sessionId, runtime);
  return runtime;
}

function runtimeForProvisioner(provisioner) {
  const pinned = runtimesByProvisioner.get(provisioner);
  if (pinned) return pinned;
  // Session id first: in a worker the provisioner prototype is shared, so only
  // the id distinguishes sessions. The prototype is the next best key when a
  // host exposes no id, and beats fallbackRuntime because that one is
  // first-install-wins for the whole worker.
  const runtime =
    runtimesBySession.get(sessionIdOf(provisioner)) ??
    runtimesByProvisionerPrototype.get(Object.getPrototypeOf(provisioner ?? {})) ??
    fallbackRuntime;
  if (runtime && provisioner && typeof provisioner === "object") {
    runtimesByProvisioner.set(provisioner, runtime);
  }
  return runtime;
}

/** Test seam: drop worker-level routing so a suite can install a fresh set of runtimes. */
export function resetYieldRouting() {
  runtimesBySession.clear();
  fallbackRuntime = undefined;
}

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
  const runtime = {
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
      if (!manager || typeof manager !== "object") return;
      readyManagers.add(manager);
      // Pin routing here as well as in patchedEnsure. A manager whose prototype
      // was patched directly, without going through the provisioner, would
      // otherwise resolve through fallbackRuntime and end up booking its cells
      // in whichever session's registry happened to install first.
      runtimesByManager.set(manager, runtime);
    },
    isReady(manager) {
      return Boolean(manager) && readyManagers.has(manager);
    },
  };
  return runtime;
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
  if (runtime) registerRuntime(runtime, undefined);
  if (prototype?.[PATCH_STATE]?.[EXECUTE_METHOD]) return "already-installed";
  const descriptor = assertPatchable(prototype, EXECUTE_METHOD, "KernelManager");
  const original = descriptor.value;

  async function patchedExecute(code, opts = {}) {
    const options = opts ?? {};
    // Resolved per call, not captured: this prototype is shared by every
    // session in the worker (see the routing tables above).
    const runtime = runtimesByManager.get(this) ?? fallbackRuntime;
    if (!runtime) return original.call(this, code, options);
    const { registry } = runtime;

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
export function installIpythonYield(prototype, runtime, options = {}) {
  // Register unconditionally: a later session must not be discarded just
  // because an earlier one already patched the shared prototype.
  registerRuntime(runtime, options.sessionId);
  if (prototype && typeof prototype === "object") {
    runtimesByProvisionerPrototype.set(prototype, runtime);
  }
  if (prototype?.[PATCH_STATE]?.[ENSURE_METHOD]) return "already-installed";
  const descriptor = assertPatchable(prototype, ENSURE_METHOD, "IpythonKernelProvisioner");
  const original = descriptor.value;

  async function patchedEnsure(...args) {
    const manager = await original.apply(this, args);
    if (manager && typeof manager === "object") {
      // Whichever session owns this provisioner owns the manager it returns.
      const owner = runtimeForProvisioner(this) ?? runtime;
      try {
        installKernelExecuteYield(Object.getPrototypeOf(manager), owner);
      } catch (error) {
        // A shape change must degrade to stock behaviour, not break the kernel.
        owner.onError(error);
      }
      runtimesByManager.set(manager, owner);
      owner.markReady(manager);
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
