/**
 * Settle-notice construction for the ipython-yield extension.
 *
 * Kept free of Prime Agent imports (like `registry.js` and `attach.js`) so the
 * whole notice path can be unit-tested standalone.
 *
 * Two properties matter, and both are regressions we have actually observed:
 *
 * 1. The notice must not STARVE an in-flight `ipython_attach`. `settle()` runs
 *    its listeners synchronously, while `attachCell` resumes on a microtask
 *    after `waitForSettle` resolves. A listener that reads output during the
 *    settle loop therefore always wins, no matter which listener registered
 *    first -- so registration order is not a fix. Deferring by one MACROtask
 *    puts the notice strictly after any already-queued microtask.
 *
 * 2. The notice must not CONSUME output. It peeks, so whatever it reports stays
 *    recoverable through a later attach if the tool call that should have
 *    delivered it was aborted.
 *
 * Together these make the body self-regulating: if an attach collected the
 * output, the peek is empty and the notice degrades to a one-line wake; if
 * nothing collected it, the notice carries it, which is the only delivery on
 * the `bg.run` + end-turn path.
 */

import { describeOutcome } from "./attach.js";

/** One macrotask, i.e. strictly after any pending microtask continuation. */
function macrotask(fn) {
  const timer = setTimeout(fn, 0);
  if (timer && typeof timer === "object" && "unref" in timer) timer.unref();
  return timer;
}

/**
 * Build the custom message for a settled cell.
 *
 * @param {import("./registry.js").DetachedCell} cell
 * @param {string} tail Undelivered output, already peeked (never consumed).
 */
export function buildSettleNotice(cell, tail) {
  const body = tail ? ["", tail] : [];
  return {
    customType: "ipython-yield:completed",
    content: [describeOutcome(cell), ...body].join("\n"),
    display: true,
    details: {
      cellId: cell.id,
      state: cell.state,
      elapsedMs: cell.elapsedMs,
      pendingChars: tail.length,
    },
  };
}

/**
 * Build the `onSettled` listener that wakes the session.
 *
 * @param {object} options
 * @param {() => number} options.maxChars Same cap the tools use; the notice used
 *   to hard-code 2_000 while consuming the cursor, which permanently discarded
 *   everything before the last 2k characters.
 * @param {(message: object) => void} options.send Delivers the custom message.
 * @param {(fn: () => void) => unknown} [options.defer] Seam for tests.
 */
export function createSettleNotice({ maxChars, send, defer = macrotask }) {
  return function onCellSettled(cell) {
    defer(() => {
      send(buildSettleNotice(cell, cell.peek(maxChars())));
    });
  };
}
