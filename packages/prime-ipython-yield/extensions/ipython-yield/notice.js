/**
 * Settle-notice construction for the ipython-yield extension.
 *
 * Kept free of Prime Agent imports (like `registry.js` and `attach.js`) so the
 * whole notice path can be unit-tested standalone.
 *
 * The notice is emitted SYNCHRONOUSLY inside `registry.settle()`'s handler
 * loop. It needs no timer, which matters more than it looks: an earlier
 * revision deferred by one macrotask using an unref'd `setTimeout`, and an
 * unref'd timer does not hold the event loop open. On an idle loop the wake was
 * therefore silently DROPPED -- precisely the `bg.run` + end-turn path where
 * this notice is the only delivery, and a dropped wake is indistinguishable
 * from a cell that never finished. Being synchronous also keeps the callback
 * inside the settle loop's try/catch, so a throw here cannot escape as an
 * uncaught exception.
 *
 * Whether the body is included is decided by an explicit CLAIM, not by winning
 * a race:
 *
 *  - A consumer has claimed the cell -- an `ipython_attach` or `ipython_cancel`
 *    waiting in `waitForSettle` -- so it will deliver the bytes itself the
 *    moment it resumes. The notice emits a bare wake and does not read at all,
 *    so it cannot starve the consumer.
 *  - Nobody claimed it, so the notice carries the output as its only delivery.
 *    It reads with the non-consuming `peek()`, leaving the cursor untouched, so
 *    a later attach can still recover the same bytes if this message is lost or
 *    the turn it wakes is aborted.
 */

import { describeOutcome } from "./attach.js";

/** Truncated reads are prefixed by `read()`; the omitted head is unrecoverable. */
function isTruncated(tail) {
  return tail.startsWith("[... ");
}

/**
 * Build the custom message for a settled cell.
 *
 * @param {import("./registry.js").DetachedCell} cell
 * @param {string} tail Undelivered output, already peeked (never consumed).
 */
export function buildSettleNotice(cell, tail) {
  const lines = [describeOutcome(cell)];
  if (tail) {
    lines.push("", tail);
    // Only claim completeness when the read was not truncated: with a truncated
    // read the head is already gone, so attaching cannot recover it either.
    if (!isTruncated(tail)) {
      lines.push("", `Output above is complete; no ipython_attach(cell="${cell.id}") needed.`);
    }
  }
  return {
    customType: "ipython-yield:completed",
    content: lines.join("\n"),
    display: true,
    details: {
      cellId: cell.id,
      state: cell.state,
      elapsedMs: cell.elapsedMs,
      /** Characters carried by THIS message, after any truncation. */
      pendingChars: tail.length,
      /** Characters no consumer can recover, dropped by a truncating read. */
      droppedChars: cell.droppedChars,
      /** True when a waiting attach/cancel will deliver the output instead. */
      claimed: cell.claimed,
    },
  };
}

/**
 * Build the `onSettled` listener that wakes the session.
 *
 * @param {object} options
 * @param {() => number} options.maxChars Same cap the tools use. The notice
 *   previously hard-coded 2_000 while consuming the cursor, which permanently
 *   discarded everything before the last 2k characters.
 * @param {(message: object) => void} options.send Delivers the custom message.
 * @param {() => void} [options.prune] Drops old settled cells. Without this the
 *   notice-only path -- the common one -- never prunes, because only attach and
 *   cancel do, and the registry grows for the life of the session.
 */
export function createSettleNotice({ maxChars, send, prune }) {
  return function onCellSettled(cell) {
    const tail = cell.claimed ? "" : cell.peek(maxChars());
    send(buildSettleNotice(cell, tail));
    prune?.();
  };
}
