/**
 * Detached-cell bookkeeping for the ipython-yield extension.
 *
 * Pure data structure with no Prime Agent imports so it can be unit-tested
 * standalone. The patch layer feeds it streaming output and settlement events;
 * the tool layer reads incremental tails out of it.
 */

/** Wall-clock seconds, one decimal, for model-facing banners. */
function seconds(ms) {
  return `${(ms / 1000).toFixed(1)}s`;
}

function clampInt(value, min, max, fallback) {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(n)));
}

/**
 * One cell that outlived its tool call.
 *
 * `stdout`/`stderr` accumulate everything the kernel streamed. `deliveredOut`
 * and `deliveredErr` track how much of that has already been shown to the
 * model, so every re-attach returns only new bytes.
 */
export class DetachedCell {
  constructor(id, code, now) {
    this.id = id;
    this.code = code;
    this.startedAt = now;
    this.stdout = "";
    this.stderr = "";
    this.deliveredOut = 0;
    this.deliveredErr = 0;
    /** "running" | "detached" | "settled" | "failed" */
    this.state = "running";
    this.result = undefined;
    this.error = undefined;
    this.settledAt = undefined;
    /** Aborting this cancels the underlying cell through the kernel's own interrupt path. */
    this.controller = undefined;
    /** The original execute() promise, kept alive after the tool call returns. */
    this.inner = undefined;
  }

  get elapsedMs() {
    return (this.settledAt ?? Date.now()) - this.startedAt;
  }

  append(chunk, name) {
    if (typeof chunk !== "string" || chunk.length === 0) return;
    if (name === "stderr") this.stderr += chunk;
    else this.stdout += chunk;
  }

  /** Undelivered output, marking it delivered. */
  drain(maxChars) {
    const limit = clampInt(maxChars, 256, 200_000, 16_000);
    const out = this.stdout.slice(this.deliveredOut);
    const err = this.stderr.slice(this.deliveredErr);
    this.deliveredOut = this.stdout.length;
    this.deliveredErr = this.stderr.length;
    let text = out;
    if (err) text += (text ? "\n" : "") + err;
    if (text.length <= limit) return text;
    // Keep the tail: the end of a long-running cell's output is what matters.
    return `[... ${text.length - limit} earlier chars omitted ...]\n${text.slice(-limit)}`;
  }
}

export class DetachedCellRegistry {
  constructor(options = {}) {
    this.cells = new Map();
    this.counter = 0;
    this.onSettledHandlers = new Set();
    this.now = options.now ?? (() => Date.now());
  }

  create(code) {
    this.counter += 1;
    const cell = new DetachedCell(`cell_${this.counter}`, code, this.now());
    this.cells.set(cell.id, cell);
    return cell;
  }

  get(id) {
    if (id instanceof DetachedCell) return id;
    return this.cells.get(String(id ?? "").trim());
  }

  /** The one cell currently holding the kernel, if any. */
  active() {
    for (const cell of this.cells.values()) {
      if (cell.state === "detached") return cell;
    }
    return undefined;
  }

  onSettled(handler) {
    this.onSettledHandlers.add(handler);
    return () => this.onSettledHandlers.delete(handler);
  }

  markDetached(cell) {
    if (cell.state === "running") cell.state = "detached";
    return cell;
  }

  /**
   * Record a cell's outcome. Fires settle handlers only for cells the model was
   * told about (i.e. actually detached), so ordinary fast cells stay silent.
   */
  settle(cell, { result, error } = {}) {
    const wasDetached = cell.state === "detached";
    cell.settledAt = this.now();
    cell.result = result;
    cell.error = error;
    cell.state = error ? "failed" : "settled";
    if (!wasDetached) {
      // Never detached: the tool call already returned the real result.
      this.cells.delete(cell.id);
      return false;
    }
    for (const handler of this.onSettledHandlers) {
      try {
        handler(cell);
      } catch {
        // A misbehaving listener must not break kernel bookkeeping.
      }
    }
    return true;
  }

  /** Drop settled cells, keeping the most recent `keep` for late re-attach. */
  prune(keep = 8) {
    const finished = [...this.cells.values()].filter((c) => c.state === "settled" || c.state === "failed");
    finished.sort((a, b) => (a.settledAt ?? 0) - (b.settledAt ?? 0));
    for (const cell of finished.slice(0, Math.max(0, finished.length - keep))) {
      this.cells.delete(cell.id);
    }
  }
}

/**
 * Synthetic ExecuteResult handed back when a cell is detached mid-flight.
 * status stays "ok": the cell is healthy, it just has not finished.
 */
export function detachedResult(cell, maxChars) {
  const body = cell.drain(maxChars);
  const banner = [
    `<ipython_cell_detached id="${cell.id}" elapsed="${seconds(cell.elapsedMs)}">`,
    body || "(no output yet)",
    "</ipython_cell_detached>",
    "",
    "This cell is STILL RUNNING and the IPython kernel stays busy until it finishes.",
    "New ipython cells cannot run until it ends. Do NOT poll in a loop.",
    "Preferred: answer the user / handle pending messages, then end your turn -- you are woken",
    `automatically when ${cell.id} completes. Otherwise: ipython_attach(cell="${cell.id}")`,
    `to wait a bounded time for it, or ipython_cancel(cell="${cell.id}") to interrupt it.`,
  ].join("\n");
  return {
    stdout: banner,
    stderr: "",
    status: "ok",
    durationMs: cell.elapsedMs,
    diffs: [],
    attachments: [],
    sentAgentMessages: [],
  };
}

/**
 * Synthetic ExecuteResult for a cell submitted while a detached cell owns the
 * kernel. status is "error" so the model cannot mistake this for execution.
 */
export function busyResult(cell, maxChars) {
  const body = cell.drain(maxChars);
  const banner = [
    `<ipython_kernel_busy cell="${cell.id}" elapsed="${seconds(cell.elapsedMs)}">`,
    body || "(no new output)",
    "</ipython_kernel_busy>",
    "",
    "Your code was NOT executed. The kernel is still running the detached cell above.",
    `Wait for it (end your turn, or ipython_attach(cell="${cell.id}")), or stop it with`,
    `ipython_cancel(cell="${cell.id}"). Re-submit your code once the kernel is free.`,
  ].join("\n");
  return {
    stdout: banner,
    stderr: "",
    status: "error",
    error: { ename: "IpythonKernelBusy", evalue: `${cell.id} is still running`, traceback: [] },
    durationMs: 0,
    diffs: [],
    attachments: [],
    sentAgentMessages: [],
  };
}

export { clampInt, seconds };
