/**
 * Cap how long one IPython cell may hold Prime Agent's only tool.
 *
 * A cell that runs longer than the yield cap is *detached*: the tool call
 * returns with the output so far, the cell keeps running untouched, and the
 * model regains control to answer the user or absorb queued agent messages.
 * When the cell finally settles the session is woken with the outcome.
 *
 * Configure with PRIME_IPYTHON_YIELD_MS (0 disables yielding entirely).
 */

import { IpythonKernelProvisioner } from "@earendil-works/pi-coding-agent";

import { createYieldRuntime, installIpythonYield } from "./patch.js";
import { createSettleNotice } from "./notice.js";
import { createAttachTool, createCancelTool } from "./tools.js";

function envNumber(name) {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

export default function ipythonYieldExtension(pi) {
  const runtime = createYieldRuntime({
    yieldMs: () => envNumber("PRIME_IPYTHON_YIELD_MS"),
    maxChars: () => envNumber("PRIME_IPYTHON_YIELD_MAX_CHARS"),
    onError: (error) => {
      // Surfaced once; the kernel keeps its stock blocking behaviour.
      console.error(`[ipython-yield] disabled: ${error instanceof Error ? error.message : String(error)}`);
    },
  });

  installIpythonYield(IpythonKernelProvisioner.prototype, runtime);

  pi.registerTool(createAttachTool(runtime));
  pi.registerTool(createCancelTool(runtime));

  // The wake-up: without this a detached cell would finish silently while the
  // session sat idle, and the model would never collect its result. The notice
  // peeks one macrotask after settlement so it never takes output an in-flight
  // ipython_attach is waiting for -- see notice.js.
  runtime.registry.onSettled(
    createSettleNotice({
      maxChars: () => runtime.maxChars(),
      send: (message) => pi.sendMessage(message, { triggerTurn: true, deliverAs: "followUp" }),
    }),
  );
}

export {
  createYieldRuntime,
  installIpythonYield,
  installKernelExecuteYield,
  PATCH_STATE,
  restoreIpythonYield,
} from "./patch.js";
