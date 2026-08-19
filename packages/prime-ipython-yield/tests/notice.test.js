import assert from "node:assert/strict";
import test from "node:test";

import { attachCell } from "../extensions/ipython-yield/attach.js";
import { buildSettleNotice, createSettleNotice } from "../extensions/ipython-yield/notice.js";
import { createYieldRuntime } from "../extensions/ipython-yield/patch.js";

/**
 * Wire the notice exactly as index.js does, capturing what would be sent.
 * `defer` stays the real macrotask: the ordering guarantee is the thing under test.
 */
function runtimeWithNotice({ maxChars = 16_000 } = {}) {
  const runtime = createYieldRuntime({ yieldMs: 20, minYieldMs: 1, maxChars });
  const sent = [];
  const settled = [];
  runtime.registry.onSettled(
    createSettleNotice({
      maxChars: () => runtime.maxChars(),
      send: (message) => {
        sent.push(message);
        for (const resolve of settled.splice(0)) resolve(message);
      },
    }),
  );
  const nextNotice = () => new Promise((resolve) => settled.push(resolve));
  const cell = runtime.registry.create("time.sleep(300)");
  runtime.registry.markDetached(cell);
  return { runtime, cell, sent, nextNotice };
}

// The cell_89 regression: attach waited, the notice drained first, and attach
// truthfully reported "(no new output)" while the notice carried the bytes.
test("a cell settling under a waiting attach gives its output to the attach", async () => {
  const { runtime, cell, nextNotice } = runtimeWithNotice();
  const attaching = attachCell(runtime, { timeout_ms: 5_000 });

  cell.append("THE OUTPUT\n", "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  const { text } = await attaching;
  assert.match(text, /THE OUTPUT/, "attach must receive the output it was waiting for");
  assert.doesNotMatch(text, /no new output/);

  const notice = await nextNotice();
  assert.equal(notice.details.pendingChars, 0, "nothing was left for the notice to report");
  assert.doesNotMatch(notice.content, /THE OUTPUT/, "the notice must not repeat delivered output");
  assert.match(notice.content, /completed successfully/, "but it must still wake the session");
});

// The cell_114 good path: bg.run + end turn, nobody attached, so the notice is
// the only delivery there will ever be.
test("a cell settling with no attach carries its output in the notice", async () => {
  const { runtime, cell, nextNotice } = runtimeWithNotice();
  cell.append("SOLE DELIVERY\n", "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  const notice = await nextNotice();
  assert.match(notice.content, /SOLE DELIVERY/);
  assert.equal(notice.details.pendingChars, "SOLE DELIVERY\n".length);
});

// Abort safety: the notice peeks, so an aborted tool call cannot strand output.
test("the notice never consumes the cursor, so a later attach still recovers the output", async () => {
  const { runtime, cell, nextNotice } = runtimeWithNotice();
  cell.append("RECOVERABLE\n", "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  const notice = await nextNotice();
  assert.match(notice.content, /RECOVERABLE/);

  const { text } = await attachCell(runtime, { cell: cell.id, timeout_ms: 0 });
  assert.match(text, /RECOVERABLE/, "peeked output must remain available to attach");
});

test("the notice honours the configured cap instead of a hard-coded 2k", async () => {
  const { runtime, cell, nextNotice } = runtimeWithNotice({ maxChars: 4_000 });
  cell.append("x".repeat(3_000), "stdout");
  runtime.registry.settle(cell, { result: { status: "ok" } });

  const notice = await nextNotice();
  assert.equal(notice.details.pendingChars, 3_000, "3k of output must survive a 4k cap");
  assert.doesNotMatch(notice.content, /earlier chars omitted/);
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
