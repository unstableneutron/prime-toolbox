import { AgentSession } from "@earendil-works/pi-coding-agent";

import { installSubagentFastModeDefaultOff } from "./patch.js";

/**
 * Make standard service tier (Fast mode off) the default for RLM subagents.
 */
export default function subagentFastModeExtension(_pi) {
  installSubagentFastModeDefaultOff(AgentSession.prototype);
}

export {
  installSubagentFastModeDefaultOff,
  PATCH_STATE,
  restoreSubagentFastModeDefault,
} from "./patch.js";
