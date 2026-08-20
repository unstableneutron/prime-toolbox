import assert from "node:assert/strict";
import test from "node:test";

import { busyResult, DetachedCellRegistry, detachedResult } from "../extensions/ipython-yield/registry.js";

test("drain returns only output produced since the last look", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  cell.append("first\n", "stdout");
  assert.equal(cell.drain(), "first\n");
  // Nothing new: a re-attach must not re-deliver what the model already saw.
  assert.equal(cell.drain(), "");
  cell.append("second\n", "stdout");
  assert.equal(cell.drain(), "second\n");
});

test("drain keeps the tail when output exceeds the cap", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  cell.append("x".repeat(5000) + "END", "stdout");
  const text = cell.drain(300);
  assert.ok(text.endsWith("END"), "tail must be preserved");
  assert.match(text, /earlier chars omitted/);
});

test("stderr is folded in after stdout", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  cell.append("out", "stdout");
  cell.append("err", "stderr");
  assert.equal(cell.drain(), "out\nerr");
});

test("active() reports only a detached cell", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  assert.equal(registry.active(), undefined, "a running-but-attached cell does not own the kernel");
  registry.markDetached(cell);
  assert.equal(registry.active(), cell);
  registry.settle(cell, { result: { status: "ok" } });
  assert.equal(registry.active(), undefined);
});

test("settling a never-detached cell is silent and forgets it", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  let notified = 0;
  registry.onSettled(() => notified++);

  assert.equal(registry.settle(cell, { result: { status: "ok" } }), false);
  assert.equal(notified, 0, "fast cells must not wake the session");
  assert.equal(registry.get(cell.id), undefined);
});

test("settling a detached cell notifies listeners", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  registry.markDetached(cell);
  const seen = [];
  registry.onSettled((c) => seen.push(c.id));

  assert.equal(registry.settle(cell, { result: { status: "ok" } }), true);
  assert.deepEqual(seen, [cell.id]);
  assert.equal(registry.get(cell.id), cell, "settled cells stay readable for a late attach");
});

test("a listener that throws cannot break bookkeeping", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  registry.markDetached(cell);
  registry.onSettled(() => {
    throw new Error("boom");
  });
  assert.equal(registry.settle(cell, { result: { status: "ok" } }), true);
  assert.equal(cell.state, "settled");
});

test("prune keeps recent finished cells and drops older ones", () => {
  const registry = new DetachedCellRegistry();
  for (let i = 0; i < 5; i++) {
    const cell = registry.create(`code${i}`);
    registry.markDetached(cell);
    registry.settle(cell, { result: { status: "ok" } });
  }
  registry.prune(2);
  assert.equal(registry.cells.size, 2);
});

test("detachedResult is a non-error ExecuteResult naming the cell", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("time.sleep(300)");
  cell.append("partial", "stdout");
  const result = detachedResult(cell);

  assert.equal(result.status, "ok", "a detached cell is healthy, not failed");
  assert.match(result.stdout, /ipython_cell_detached/);
  assert.match(result.stdout, /partial/);
  assert.match(result.stdout, new RegExp(cell.id));
  assert.deepEqual(result.diffs, []);
});

test("busyResult is an error so the model cannot mistake it for execution", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  const result = busyResult(cell);

  assert.equal(result.status, "error");
  assert.equal(result.error.ename, "IpythonKernelBusy");
  assert.match(result.stdout, /was NOT executed/);
});

test("peek returns pending output without consuming it", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  cell.append("first\n", "stdout");

  assert.equal(cell.peek(), "first\n");
  assert.equal(cell.peek(), "first\n", "peek must not advance the cursor");
  assert.equal(cell.drain(), "first\n", "the bytes are still there for a real consumer");
  assert.equal(cell.peek(), "", "and are gone once actually delivered");
});

test("claims are counted and released independently", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  assert.equal(cell.claimed, false);

  const releaseA = cell.claim();
  const releaseB = cell.claim();
  assert.equal(cell.claimed, true);

  releaseA();
  releaseA(); // releasing twice must not double-decrement
  assert.equal(cell.claimed, true, "B still holds a claim");

  releaseB();
  assert.equal(cell.claimed, false);
});

test("a truncating consuming read records the head it destroyed", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  cell.append("z".repeat(2_000), "stdout");

  const text = cell.drain(300);
  assert.match(text, /earlier chars omitted/);
  assert.equal(cell.droppedChars, 1_700, "the omitted head is gone, and must be accounted for");
  assert.equal(cell.drain(300), "", "the cursor moved past it, so nothing can recover it");
});

test("peeking never records drops, because it destroys nothing", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("code");
  cell.append("z".repeat(2_000), "stdout");

  cell.peek(300);
  assert.equal(cell.droppedChars, 0);
  assert.equal(cell.drain(4_000).length, 2_000, "a later consumer still gets everything");
});

test("busyResult does not steal output from the detached cell's own attach", () => {
  const registry = new DetachedCellRegistry();
  const cell = registry.create("time.sleep(300)");
  registry.markDetached(cell);
  cell.append("DETACHED OUTPUT\n", "stdout");

  const busy = busyResult(cell, 16_000);
  assert.match(busy.stdout, /DETACHED OUTPUT/, "the banner still shows progress");
  assert.equal(cell.drain(16_000), "DETACHED OUTPUT\n", "but an attach must still receive it");
});
