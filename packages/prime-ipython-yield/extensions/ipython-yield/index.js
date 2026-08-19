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
import { describeOutcome } from "./attach.js";
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
  // session sat idle, and the model would never collect its result.
  runtime.registry.onSettled((cell) => {
    const tail = cell.drain(2_000);
    pi.sendMessage(
      {
        customType: "ipython-yield:completed",
        content: [describeOutcome(cell), "", tail || "(no further output)"].join("\n"),
        display: true,
        details: { cellId: cell.id, state: cell.state, elapsedMs: cell.elapsedMs },
      },
      { triggerTurn: true, deliverAs: "followUp" },
    );
  });
}

export {
  createYieldRuntime,
  installIpythonYield,
  installKernelExecuteYield,
  PATCH_STATE,
  restoreIpythonYield,
} from "./patch.js";
