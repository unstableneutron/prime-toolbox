/**
 * Detach-recovery logic, kept free of Prime Agent and TypeBox imports so it can
 * be unit-tested standalone. `tools.js` wraps these in tool definitions.
 */

import { seconds } from "./registry.js";

export const MAX_ATTACH_WAIT_MS = 60_000;
export const DEFAULT_ATTACH_WAIT_MS = 15_000;
const CANCEL_SETTLE_WAIT_MS = 5_000;

export function clampWait(value, fallback = DEFAULT_ATTACH_WAIT_MS) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(MAX_ATTACH_WAIT_MS, Math.max(0, Math.trunc(n)));
}

export function isFinished(cell) {
  return cell.state === "settled" || cell.state === "failed";
}

/** Resolve when the cell settles, or when the bounded timeout expires. */
export function waitForSettle(registry, cell, timeoutMs) {
  if (isFinished(cell) || timeoutMs <= 0) return Promise.resolve(cell);
  return new Promise((resolve) => {
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      off();
      clearTimeout(timer);
      resolve(cell);
    };
    const off = registry.onSettled((settledCell) => {
      if (settledCell.id === cell.id) finish();
    });
    const timer = setTimeout(finish, timeoutMs);
    if (timer && typeof timer === "object" && "unref" in timer) timer.unref();
  });
}

/** Human-readable outcome line for a finished cell. */
export function describeOutcome(cell) {
  if (cell.state === "failed") {
    const message = cell.error instanceof Error ? cell.error.message : String(cell.error ?? "unknown error");
    return `${cell.id} FAILED after ${seconds(cell.elapsedMs)}: ${message}`;
  }
  const status = cell.result?.status ?? "ok";
  if (status === "error") {
    const trace = cell.result?.error?.traceback?.join("\n");
    return `${cell.id} finished with an ERROR after ${seconds(cell.elapsedMs)}${trace ? `\n${trace}` : ""}`;
  }
  if (status === "aborted") return `${cell.id} was ABORTED after ${seconds(cell.elapsedMs)}`;
  return `${cell.id} completed successfully in ${seconds(cell.elapsedMs)}`;
}

/** Most recently settled cell, so a bare attach after the wake still works. */
function mostRecentlyFinished(registry) {
  let best;
  for (const cell of registry.cells.values()) {
    if (!isFinished(cell)) continue;
    if (!best || (cell.settledAt ?? 0) >= (best.settledAt ?? 0)) best = cell;
  }
  return best;
}

export function pickCell(registry, requested) {
  if (requested) {
    const cell = registry.get(requested);
    if (!cell) {
      const known = [...registry.cells.keys()].join(", ") || "(none)";
      return { error: `Unknown cell "${requested}". Known cells: ${known}` };
    }
    return { cell };
  }
  const active = registry.active();
  if (active) return { cell: active };
  // The model is typically woken by a completion and then attaches with no id.
  const finished = mostRecentlyFinished(registry);
  if (finished) return { cell: finished };
  return { error: "No detached cell is currently running. The kernel is free; call ipython directly." };
}

/** @returns {Promise<{text: string, details: object, isError: boolean}>} */
export async function attachCell(runtime, params = {}) {
  const { registry } = runtime;
  const picked = pickCell(registry, params.cell);
  if (picked.error) return { text: picked.error, details: { status: "unknown-cell" }, isError: true };
  const cell = picked.cell;

  await waitForSettle(registry, cell, clampWait(params.timeout_ms));

  const body = cell.drain(runtime.maxChars());
  const done = isFinished(cell);
  const header = done ? describeOutcome(cell) : `${cell.id} is still running after ${seconds(cell.elapsedMs)}.`;
  const footer = done
    ? "The kernel is free again; you can run new ipython cells."
    : `Still running. End your turn -- you are woken when ${cell.id} completes. Do not poll.`;

  if (done) registry.prune();
  return {
    text: [header, "", body || "(no new output)", "", footer].join("\n"),
    details: { cellId: cell.id, state: cell.state, elapsedMs: cell.elapsedMs, done },
    isError: cell.state === "failed" || cell.result?.status === "error",
  };
}

/** @returns {Promise<{text: string, details: object, isError: boolean}>} */
export async function cancelCell(runtime, params = {}) {
  const { registry } = runtime;
  const picked = pickCell(registry, params.cell);
  if (picked.error) return { text: picked.error, details: { status: "unknown-cell" }, isError: true };
  const cell = picked.cell;

  if (isFinished(cell)) {
    return {
      text: `${describeOutcome(cell)} (nothing to cancel)`,
      details: { cellId: cell.id, state: cell.state, done: true },
      isError: false,
    };
  }
  if (!cell.controller) {
    return {
      text: `${cell.id} cannot be interrupted from here. Restart the kernel if it must be stopped.`,
      details: { cellId: cell.id, state: cell.state, done: false },
      isError: true,
    };
  }

  cell.controller.abort();
  // The kernel interrupts, then force-resolves after its own abort grace period.
  await waitForSettle(registry, cell, runtime.cancelWaitMs?.() ?? CANCEL_SETTLE_WAIT_MS);
  const body = cell.drain(runtime.maxChars());
  const done = isFinished(cell);
  if (done) registry.prune();
  return {
    text: [
      done ? `${cell.id} stopped after ${seconds(cell.elapsedMs)}.` : `Interrupt sent to ${cell.id}; it has not stopped yet.`,
      "",
      body || "(no additional output)",
    ].join("\n"),
    details: { cellId: cell.id, state: cell.state, done },
    isError: false,
  };
}
