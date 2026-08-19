/**
 * Model-facing tools for recovering a detached cell.
 *
 * Registered as siblings of the built-in `ipython` tool rather than as extra
 * parameters on it: replacing the built-in definition would mean rebuilding its
 * host wiring (hostHandlers, pythonSkills, snapshotDir), which an extension
 * cannot see.
 *
 * Note the import specifier: extensions loaded inside the esbuild CLI bundle
 * resolve pi packages through VIRTUAL_MODULES, which maps "@sinclair/typebox"
 * but NOT bare "typebox".
 */

import { Type } from "@sinclair/typebox";

import { attachCell, cancelCell } from "./attach.js";

const ATTACH_PARAMS = Type.Object({
  cell: Type.Optional(
    Type.String({
      description: 'Detached cell id, e.g. "cell_1". Defaults to the cell currently holding the kernel.',
    }),
  ),
  timeout_ms: Type.Optional(
    Type.Number({
      description: "Bounded wait for completion, 0-60000ms. Default 15000. Returns new output either way.",
    }),
  ),
});

const CANCEL_PARAMS = Type.Object({
  cell: Type.Optional(
    Type.String({ description: "Detached cell id to interrupt. Defaults to the cell holding the kernel." }),
  ),
});

function toToolResult({ text, details, isError }) {
  return { content: [{ type: "text", text }], details, isError };
}

export function createAttachTool(runtime) {
  return {
    name: "ipython_attach",
    label: "ipython attach",
    description:
      "Re-attach to an IPython cell that was detached because it exceeded the yield cap. Waits a bounded time for it to finish and returns only output produced since the last look. Does not re-run anything.",
    promptSnippet: "ipython_attach - collect output from a still-running detached ipython cell",
    promptGuidelines: [
      "Use ipython_attach only after an ipython call reported a detached cell.",
      "Never call ipython_attach in a polling loop; end the turn instead and you are woken when the cell completes.",
    ],
    parameters: ATTACH_PARAMS,
    executionMode: "sequential",
    async execute(_toolCallId, params) {
      return toToolResult(await attachCell(runtime, params ?? {}));
    },
  };
}

export function createCancelTool(runtime) {
  return {
    name: "ipython_cancel",
    label: "ipython cancel",
    description:
      "Interrupt a detached IPython cell (equivalent to Ctrl+C inside the kernel) and free the kernel for new cells.",
    promptSnippet: "ipython_cancel - interrupt a detached ipython cell",
    parameters: CANCEL_PARAMS,
    executionMode: "sequential",
    async execute(_toolCallId, params) {
      return toToolResult(await cancelCell(runtime, params ?? {}));
    },
  };
}
