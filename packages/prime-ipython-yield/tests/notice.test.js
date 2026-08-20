import assert from "node:assert/strict";
import test from "node:test";

import { attachCell } from "../extensions/ipython-yield/attach.js";
import { buildSettleNotice, createSettleNotice } from "../extensions/ipython-yield/notice.js";
import { createYieldRuntime } from "../extensions/ipython-yield/patch.js";

/** Wire the notice exactly as index.js does, capturing what would be sent. */
function runtimeWithNotice({ maxChars = 16_000 } = {}) {
  const runtime = createYieldRuntime({ yieldMs: 20, minYieldMs: 1, maxChars });
  const sent = [];
  runtime.registry.onSettled(
    createSettleNotice({
      maxChars: () => runtime.maxChars(),
      send: (message) => sent.push(message),
      prune: () => runtime.registry.prune(),
    }),
  );
  const cell = runtime.registry.create("time.sleep(300)");
  runtime.registry.markDetached(cell);
  return { runtime, cell, sent };
}

// The cell_89 regression: attach waited, the notice took the bytes first, and
// attach truthfully reported "(no new output)".
test("a cell settling under a waiting attach gives its output to the attach", async () => {
  const { runtime, cell, sent } = runtimeWithNotice();
  const attaching = attachCell(runtime, { timeout_ms: 5_000 });

  cell.append("THE OUTPUT\n", "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  const { text } = await attaching;
  assert.match(text, /THE OUTPUT/, "attach must receive the output it was waiting for");
  assert.doesNotMatch(text, /no new output/);

  assert.equal(sent.length, 1);
  assert.equal(sent[0].details.claimed, true, "the waiting attach had claimed the cell");
  assert.equal(sent[0].details.pendingChars, 0);
  assert.ok(!sent[0].content.includes("\n"), `expected a bare one-line wake, got: ${sent[0].content}`);
  assert.match(sent[0].content, /completed successfully/, "but it must still wake the session");
});

// The wake must be SENT, not merely scheduled. A previous revision deferred it
// on an unref'd timer, which does not hold the event loop open, so on an idle
// loop the notice was dropped entirely.
test("the wake is emitted synchronously during settle, not on a timer", () => {
  const { runtime, cell, sent } = runtimeWithNotice();
  cell.append("SOLE DELIVERY\n", "stdout");

  runtime.registry.settle(cell, { result: { status: "ok" } });

  assert.equal(sent.length, 1, "the notice must exist before settle() returns");
  assert.match(sent[0].content, /SOLE DELIVERY/);
  assert.equal(sent[0].details.pendingChars, "SOLE DELIVERY\n".length);
  assert.equal(sent[0].details.claimed, false);
});

test("the notice never consumes the cursor, so a later attach still recovers the output", async () => {
  const { runtime, cell, sent } = runtimeWithNotice();
  cell.append("RECOVERABLE\n", "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });
  assert.match(sent[0].content, /RECOVERABLE/);

  const { text } = await attachCell(runtime, { cell: cell.id, timeout_ms: 0 });
  assert.match(text, /RECOVERABLE/, "peeked output must remain available to attach");
});

test("an attach that times out leaves the notice free to carry the output", async () => {
  const { runtime, cell, sent } = runtimeWithNotice();
  const { text } = await attachCell(runtime, { cell: cell.id, timeout_ms: 1 });
  assert.match(text, /still running/i);
  assert.equal(cell.claimed, false, "a timed-out attach must release its claim");

  cell.append("AFTER TIMEOUT\n", "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });
  assert.equal(sent.length, 1);
  assert.match(sent[0].content, /AFTER TIMEOUT/);
  assert.equal(sent[0].details.claimed, false);
});

test("the notice honours the configured cap instead of a hard-coded 2k", () => {
  const { runtime, cell, sent } = runtimeWithNotice({ maxChars: 4_000 });
  cell.append("x".repeat(3_000), "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  assert.equal(sent[0].details.pendingChars, 3_000, "3k of output must survive a 4k cap");
  assert.doesNotMatch(sent[0].content, /earlier chars omitted/);
});

test("the no-attach-needed hint is withheld when the read was truncated", () => {
  const { runtime, cell, sent } = runtimeWithNotice({ maxChars: 256 });
  cell.append("y".repeat(2_000), "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  assert.match(sent[0].content, /earlier chars omitted/);
  assert.doesNotMatch(sent[0].content, /no ipython_attach/, "attaching cannot recover the dropped head");
});

test("the notice prunes, so a session that only ever lets the wake deliver stays bounded", () => {
  const runtime = createYieldRuntime({ yieldMs: 20, minYieldMs: 1 });
  runtime.registry.onSettled(
    createSettleNotice({ maxChars: () => 16_000, send: () => {}, prune: () => runtime.registry.prune() }),
  );
  for (let i = 0; i < 20; i += 1) {
    const cell = runtime.registry.create(`cell ${i}`);
    runtime.registry.markDetached(cell);
    runtime.registry.settle(cell, { result: { status: "ok" } });
  }
  assert.ok(runtime.registry.cells.size <= 8, `registry grew to ${runtime.registry.cells.size}`);
});

test("a failed cell still wakes the session with its traceback", () => {
  const runtime = createYieldRuntime({ yieldMs: 20, minYieldMs: 1 });
  const cell = runtime.registry.create("boom");
  runtime.registry.markDetached(cell);
  runtime.registry.settle(cell, {
    result: { status: "error", error: { ename: "ValueError", evalue: "bad", traceback: ["ValueError: bad"] } },
  });

  const notice = buildSettleNotice(cell, cell.peek(16_000));
  assert.match(notice.content, /ERROR/);
  assert.match(notice.content, /ValueError: bad/);
  assert.equal(notice.customType, "ipython-yield:completed");
});
