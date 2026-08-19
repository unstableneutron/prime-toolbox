import assert from "node:assert/strict";
import test from "node:test";

import { attachCell, cancelCell, clampWait, describeOutcome } from "../extensions/ipython-yield/attach.js";
import { createYieldRuntime } from "../extensions/ipython-yield/patch.js";

function runtimeWithDetachedCell(code = "time.sleep(300)") {
  const runtime = createYieldRuntime({ yieldMs: 20, minYieldMs: 1 });
  const cell = runtime.registry.create(code);
  runtime.registry.markDetached(cell);
  return { runtime, cell };
}

test("attach returns only output produced since the detach banner", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  cell.append("early\n", "stdout");
  cell.drain(); // the detach banner already showed this

  cell.append("later\n", "stdout");
  const { text, details, isError } = await attachCell(runtime, { timeout_ms: 0 });

  assert.match(text, /later/);
  assert.doesNotMatch(text, /early/, "already-delivered output must not be repeated");
  assert.equal(details.done, false);
  assert.equal(isError, false);
  assert.match(text, /still running/i);
});

test("attach reports success once the cell has settled", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  cell.append("all done\n", "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  const { text, details } = await attachCell(runtime, {});
  assert.match(text, /completed successfully/);
  assert.match(text, /all done/);
  assert.match(text, /kernel is free again/);
  assert.equal(details.done, true);
});

test("attach surfaces a cell that ended in a Python error", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  runtime.registry.settle(cell, {
    result: { status: "error", error: { ename: "ValueError", evalue: "bad", traceback: ["ValueError: bad"] } },
  });

  const { text, isError } = await attachCell(runtime, {});
  assert.match(text, /ERROR/);
  assert.match(text, /ValueError: bad/);
  assert.equal(isError, true);
});

test("attach resolves as soon as the cell settles, without burning the timeout", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  setTimeout(() => runtime.registry.settle(cell, { result: { status: "ok" } }), 20);

  const started = Date.now();
  const { details } = await attachCell(runtime, { timeout_ms: 5_000 });
  const elapsed = Date.now() - started;

  assert.equal(details.done, true);
  assert.ok(elapsed < 1_000, `expected an early return, waited ${elapsed}ms`);
});

test("attach returns promptly when the cell is still running at the timeout", async () => {
  const { runtime } = runtimeWithDetachedCell();
  const started = Date.now();
  const { details } = await attachCell(runtime, { timeout_ms: 30 });
  const elapsed = Date.now() - started;

  assert.equal(details.done, false);
  assert.ok(elapsed < 1_000, `bounded wait must not overrun, waited ${elapsed}ms`);
});

test("attach explains itself when there is nothing to attach to", async () => {
  const runtime = createYieldRuntime({});
  const { text, isError } = await attachCell(runtime, {});
  assert.match(text, /No detached cell/);
  assert.equal(isError, true);
});

test("attach names the known cells when given an unknown id", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  const { text, isError } = await attachCell(runtime, { cell: "cell_99" });
  assert.match(text, /Unknown cell "cell_99"/);
  assert.match(text, new RegExp(cell.id));
  assert.equal(isError, true);
});

test("cancel aborts the cell and reports once it stops", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  const controller = new AbortController();
  cell.controller = controller;
  controller.signal.addEventListener("abort", () => {
    runtime.registry.settle(cell, { result: { status: "aborted" } });
  });

  const { text, details } = await cancelCell(runtime, {});
  assert.equal(controller.signal.aborted, true);
  assert.equal(details.done, true);
  assert.match(text, /stopped after/);
});

test("cancel reports honestly when the cell ignores the interrupt", async () => {
  const runtime = createYieldRuntime({ yieldMs: 20, minYieldMs: 1, cancelWaitMs: 30 });
  const cell = runtime.registry.create("time.sleep(300)");
  runtime.registry.markDetached(cell);
  cell.controller = new AbortController();

  const started = Date.now();
  const { text, details } = await cancelCell(runtime, {});
  assert.ok(Date.now() - started < 1_000, "cancel must not block on an unresponsive cell");
  assert.equal(details.done, false);
  assert.match(text, /has not stopped yet/);
});

test("cancelling an already-finished cell is a no-op, not an error", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  runtime.registry.settle(cell, { result: { status: "ok" } });

  const { text, isError } = await cancelCell(runtime, {});
  assert.match(text, /nothing to cancel/);
  assert.equal(isError, false);
});

test("cells are addressable by id as well as by default", async () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  cell.append("hi\n", "stdout");
  const { details } = await attachCell(runtime, { cell: cell.id, timeout_ms: 0 });
  assert.equal(details.cellId, cell.id);
});

test("clampWait keeps waits bounded", () => {
  assert.equal(clampWait(undefined), 15_000);
  assert.equal(clampWait("nonsense"), 15_000);
  assert.equal(clampWait(999_999), 60_000);
  assert.equal(clampWait(-5), 0);
  assert.equal(clampWait(250), 250);
});

test("describeOutcome distinguishes host failure from Python error", () => {
  const { runtime, cell } = runtimeWithDetachedCell();
  runtime.registry.settle(cell, { error: new Error("kernel died") });
  assert.match(describeOutcome(cell), /FAILED/);
  assert.match(describeOutcome(cell), /kernel died/);
});
