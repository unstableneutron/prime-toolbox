import assert from "node:assert/strict";
import test from "node:test";

import {
  createYieldRuntime,
  installIpythonYield,
  installKernelExecuteYield,
  PATCH_STATE,
  resetYieldRouting,
  restoreIpythonYield,
} from "../extensions/ipython-yield/patch.js";

/** Fast test runtime: the production floor of 1s would make every test sleep. */
function testRuntime(overrides = {}) {
  return createYieldRuntime({ yieldMs: 20, minYieldMs: 1, ...overrides });
}

/**
 * Stand-in for KernelManager. `plan(code, opts)` returns the promise the real
 * execute() would return; tests drive completion explicitly.
 */
function makeManager(plan) {
  const proto = {
    async execute(code, opts = {}) {
      return this._plan(code, opts);
    },
  };
  const manager = Object.create(proto);
  manager._plan = plan;
  return { proto, manager };
}

function okResult(stdout = "") {
  return { stdout, stderr: "", status: "ok", durationMs: 1 };
}

function never() {
  return new Promise(() => {});
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test("a cell that finishes inside the cap returns its real result untouched", async () => {
  const runtime = testRuntime();
  const expected = okResult("done\n");
  const { proto, manager } = makeManager(async () => expected);
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  const result = await manager.execute("1 + 1");
  assert.equal(result, expected, "fast cells must be completely unaffected");
  assert.equal(runtime.registry.active(), undefined);
  assert.equal(runtime.registry.cells.size, 0, "fast cells leave no bookkeeping behind");
});

test("a cell that outruns the cap detaches with its partial output", async () => {
  const runtime = testRuntime();
  const { proto, manager } = makeManager((_code, opts) => {
    opts.onStream?.("halfway\n", "stdout");
    return never();
  });
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  const result = await manager.execute("time.sleep(300)");
  assert.equal(result.status, "ok");
  assert.match(result.stdout, /ipython_cell_detached/);
  assert.match(result.stdout, /halfway/);

  const active = runtime.registry.active();
  assert.ok(active, "the detached cell must still own the kernel");
  assert.equal(active.state, "detached");
});

test("the caller's onStream still fires while output is being captured", async () => {
  const runtime = testRuntime();
  const seen = [];
  const { proto, manager } = makeManager((_code, opts) => {
    opts.onStream?.("a", "stdout");
    opts.onStream?.("b", "stderr");
    return never();
  });
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  await manager.execute("code", { onStream: (chunk, name) => seen.push([chunk, name]) });
  assert.deepEqual(seen, [
    ["a", "stdout"],
    ["b", "stderr"],
  ]);
});

test("a second cell is refused instead of queueing behind the detached one", async () => {
  const runtime = testRuntime();
  let executions = 0;
  const { proto, manager } = makeManager(() => {
    executions++;
    return never();
  });
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  await manager.execute("time.sleep(300)");
  assert.equal(executions, 1);

  const refused = await manager.execute("print('hello')");
  assert.equal(refused.status, "error");
  assert.equal(refused.error.ename, "IpythonKernelBusy");
  assert.match(refused.stdout, /was NOT executed/);
  assert.equal(executions, 1, "the refused cell must never reach the kernel");
});

test("internal host cells are never detached", async () => {
  const runtime = testRuntime();
  const pending = deferred();
  const { proto, manager } = makeManager(() => pending.promise);
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  const call = manager.execute("__snapshot__", { internal: true });
  // Well past the cap: an internal cell must keep blocking, as before.
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(runtime.registry.cells.size, 0);

  const expected = okResult("snapshot");
  pending.resolve(expected);
  assert.equal(await call, expected);
});

test("cells are not detached until the kernel has finished booting", async () => {
  const runtime = testRuntime();
  const pending = deferred();
  const { proto, manager } = makeManager(() => pending.promise);
  installKernelExecuteYield(proto, runtime);
  // Deliberately NOT markReady: this models the bootstrap cell, which is a
  // normal execute() and must never be detached.

  const call = manager.execute("<bootstrap>");
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(runtime.registry.cells.size, 0);

  const expected = okResult("bootstrapped");
  pending.resolve(expected);
  assert.equal(await call, expected);
});

test("yieldMs=0 restores stock blocking behaviour", async () => {
  const runtime = createYieldRuntime({ yieldMs: 0 });
  const pending = deferred();
  const { proto, manager } = makeManager(() => pending.promise);
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  const call = manager.execute("time.sleep(300)");
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(runtime.registry.cells.size, 0, "no detach bookkeeping when disabled");

  const expected = okResult("eventually");
  pending.resolve(expected);
  assert.equal(await call, expected);
});

test("yieldMs is clamped into the supported range", () => {
  assert.equal(createYieldRuntime({ yieldMs: 5 }).yieldMs(), 1_000);
  assert.equal(createYieldRuntime({ yieldMs: 10_000_000 }).yieldMs(), 300_000);
  assert.equal(createYieldRuntime({ yieldMs: "nonsense" }).yieldMs(), 10_000);
  assert.equal(createYieldRuntime({}).yieldMs(), 10_000);
  assert.equal(createYieldRuntime({ yieldMs: 0 }).yieldMs(), 0);
});

test("a detached cell that later fails does not raise an unhandled rejection", async () => {
  const runtime = testRuntime();
  const pending = deferred();
  const { proto, manager } = makeManager(() => pending.promise);
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  await manager.execute("boom()");
  const cell = runtime.registry.active();
  pending.reject(new Error("kernel died"));
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(cell.state, "failed");
  assert.equal(cell.error.message, "kernel died");
});

test("a detached cell records its result when it finally settles", async () => {
  const runtime = testRuntime();
  const pending = deferred();
  const { proto, manager } = makeManager(() => pending.promise);
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  await manager.execute("slow()");
  const cell = runtime.registry.active();
  const woken = [];
  runtime.registry.onSettled((c) => woken.push(c.id));

  pending.resolve(okResult("finally\n"));
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(cell.state, "settled");
  assert.equal(cell.result.stdout, "finally\n");
  assert.deepEqual(woken, [cell.id], "settling a detached cell must wake the session");
  assert.equal(runtime.registry.active(), undefined, "the kernel is free again");
});

test("aborting the finished tool call cannot kill the cell the model left running", async () => {
  const runtime = testRuntime();
  let innerSignal;
  const { proto, manager } = makeManager((_code, opts) => {
    innerSignal = opts.signal;
    return never();
  });
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  const controller = new AbortController();
  await manager.execute("time.sleep(300)", { signal: controller.signal });
  assert.ok(innerSignal, "the cell must run under a signal we control");

  controller.abort();
  assert.equal(innerSignal.aborted, false, "a stale tool-call abort must not reach a detached cell");
});

test("the tool call's signal still reaches a cell that has not detached yet", async () => {
  const runtime = createYieldRuntime({ yieldMs: 5_000 });
  let innerSignal;
  const { proto, manager } = makeManager((_code, opts) => {
    innerSignal = opts.signal;
    return never();
  });
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  const controller = new AbortController();
  const call = manager.execute("time.sleep(300)", { signal: controller.signal });
  await new Promise((r) => setTimeout(r, 10));
  controller.abort();
  assert.equal(innerSignal.aborted, true, "Ctrl+C must still work before the cap elapses");
  void call;
});

test("the detached cell exposes a controller so it can be cancelled later", async () => {
  const runtime = testRuntime();
  let innerSignal;
  const { proto, manager } = makeManager((_code, opts) => {
    innerSignal = opts.signal;
    return never();
  });
  installKernelExecuteYield(proto, runtime);
  runtime.markReady(manager);

  await manager.execute("time.sleep(300)");
  const cell = runtime.registry.active();
  cell.controller.abort();
  assert.equal(innerSignal.aborted, true);
});

test("ensure() patches the manager prototype it returns and marks it ready", async () => {
  const runtime = testRuntime();
  const { proto, manager } = makeManager(() => never());
  const provisionerProto = {
    async ensure() {
      return manager;
    },
  };

  assert.equal(installIpythonYield(provisionerProto, runtime), "installed");
  const provisioner = Object.create(provisionerProto);
  assert.equal(await provisioner.ensure(), manager);

  assert.ok(proto[PATCH_STATE]?.execute, "KernelManager.prototype must be patched via the instance");
  assert.equal(runtime.isReady(manager), true);

  const result = await manager.execute("time.sleep(300)");
  assert.match(result.stdout, /ipython_cell_detached/);
});

test("installs idempotently and restores the original methods", async () => {
  const runtime = testRuntime();
  const { proto } = makeManager(() => never());
  const original = proto.execute;

  assert.equal(installKernelExecuteYield(proto, runtime), "installed");
  assert.equal(installKernelExecuteYield(proto, runtime), "already-installed");
  assert.notEqual(proto.execute, original);

  assert.equal(restoreIpythonYield(proto), true);
  assert.equal(proto.execute, original);
  assert.equal(restoreIpythonYield(proto), false);
});

test("fails loudly when the Prime Agent runtime method is unavailable", () => {
  const runtime = testRuntime();
  assert.throws(() => installKernelExecuteYield({}, runtime), /no longer exposes execute\(\)/);
  assert.throws(() => installIpythonYield({}, runtime), /no longer exposes ensure\(\)/);
  assert.throws(() => installKernelExecuteYield(null, runtime), TypeError);
});

test("a broken KernelManager shape degrades to stock behaviour instead of breaking ensure()", async () => {
  const errors = [];
  const runtime = createYieldRuntime({ yieldMs: 20, minYieldMs: 1, onError: (e) => errors.push(e) });
  const manager = Object.create({}); // no execute(): shape we cannot patch
  const provisionerProto = {
    async ensure() {
      return manager;
    },
  };
  installIpythonYield(provisionerProto, runtime);

  const provisioner = Object.create(provisionerProto);
  assert.equal(await provisioner.ensure(), manager, "ensure() must still hand back a usable kernel");
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /no longer exposes execute\(\)/);
});

// ---------------------------------------------------------------------------
// Multi-session isolation (regression: cross-session false-busy + lost wakeup)
//
// A daemon worker hosts several sessions. Each activates this extension with
// its OWN runtime/registry, but installIpythonYield/installKernelExecuteYield
// are prototype patches guarded by PATCH_STATE, so the FIRST runtime wins and
// every later session's runtime is silently discarded. Consequences:
//   1. session #1's registry books ALL sessions' cells, so registry.active()
//      returns a SIBLING session's detached cell and refuses this session's
//      executes while this session's kernel is idle;
//   2. the later session's onSettled never fires -> the completion wake is
//      delivered to session #1 (lost wakeup by misdelivery), and its
//      ipython_attach/ipython_cancel bind to an empty registry.
// ---------------------------------------------------------------------------

test("two sessions on one worker do not share a detached-cell registry", async () => {
  const provisionerProto = {
    async ensure() {
      return this._manager;
    },
  };

  // Session A: its cell will detach and stay running.
  const runtimeA = testRuntime();
  const blockedA = deferred();
  const { proto: protoA, manager: managerA } = makeManager(() => blockedA.promise);
  const provisionerA = Object.create(provisionerProto);
  provisionerA._manager = managerA;
  provisionerA.options = { sessionId: "session-A" };

  // Session B: an independent session with an idle kernel.
  const runtimeB = testRuntime();
  const { proto: protoB, manager: managerB } = makeManager(async () => okResult("B ran"));
  const provisionerB = Object.create(provisionerProto);
  provisionerB._manager = managerB;
  provisionerB.options = { sessionId: "session-B" };

  resetYieldRouting();
  installIpythonYield(provisionerProto, runtimeA, { sessionId: "session-A" });
  installIpythonYield(provisionerProto, runtimeB, { sessionId: "session-B" });

  try {
    await provisionerA.ensure();
    await provisionerB.ensure();

    // Session A detaches a long cell.
    const detached = await managerA.execute("while True: pass");
    assert.match(detached.stdout, /<ipython_cell_detached/);
    assert.equal(runtimeA.registry.active()?.state, "detached");

    // THE BUG: session B's kernel is idle, but its execute is refused because
    // session A's detached cell lives in the registry B's patch closes over.
    const resultB = await managerB.execute("print('B')");
    assert.ok(
      !/<ipython_kernel_busy/.test(resultB.stdout),
      "session B was refused because of a SIBLING session's detached cell",
    );
    assert.equal(resultB.status, "ok");

    // Registries must be disjoint: B never booked a cell, so it has none.
    assert.equal(runtimeB.registry.active(), undefined);

    // THE WAKE: session A's settle must reach session A's own listener.
    let wokeA = 0;
    let wokeB = 0;
    runtimeA.registry.onSettled(() => { wokeA += 1; });
    runtimeB.registry.onSettled(() => { wokeB += 1; });
    blockedA.resolve(okResult("A done"));
    await new Promise((r) => setTimeout(r, 5));

    assert.equal(wokeA, 1, "session A must be woken for its own cell");
    assert.equal(wokeB, 0, "session B must NOT receive session A's wake");
  } finally {
    restoreIpythonYield(provisionerProto);
    restoreIpythonYield(protoA);
    restoreIpythonYield(protoB);
    resetYieldRouting();
  }
});
