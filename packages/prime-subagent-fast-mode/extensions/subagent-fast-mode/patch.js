/**
 * Runtime patch for Prime Agent's RLM subagent spawn defaults.
 *
 * Prime Agent 0.7 inherits the parent's service tier in
 * `_createRlmSubagentRuntimeOptions`. There is no public extension hook for
 * altering those options, so this package patches that narrow method and fails
 * loudly if the runtime shape changes.
 */

export const PATCH_STATE = Symbol.for(
  "prime-toolbox:subagent-fast-mode-default-off",
);

const SPAWN_OPTIONS_METHOD = "_createRlmSubagentRuntimeOptions";

function hasExplicitFastMode(options) {
  if (!options || typeof options !== "object") return false;
  return (
    (Object.hasOwn(options, "serviceTier") && options.serviceTier !== undefined) ||
    (Object.hasOwn(options, "fastMode") && options.fastMode !== undefined)
  );
}

/**
 * Patch an AgentSession-compatible prototype.
 *
 * @param {object} prototype
 * @returns {"installed" | "already-installed"}
 */
export function installSubagentFastModeDefaultOff(prototype) {
  if (!prototype || typeof prototype !== "object") {
    throw new TypeError("Expected an AgentSession prototype object");
  }

  if (prototype[PATCH_STATE]) return "already-installed";

  const descriptor = Object.getOwnPropertyDescriptor(
    prototype,
    SPAWN_OPTIONS_METHOD,
  );
  const original = descriptor?.value;
  if (typeof original !== "function") {
    throw new Error(
      "Unsupported Prime Agent version: AgentSession no longer exposes " +
        `${SPAWN_OPTIONS_METHOD}()`,
    );
  }

  function patchedSubagentRuntimeOptions(options) {
    const result = original.call(this, options);
    if (!result || typeof result !== "object" || Array.isArray(result)) {
      throw new Error(
        `Unsupported Prime Agent version: ${SPAWN_OPTIONS_METHOD}() did not return an object`,
      );
    }

    // Preserve any future explicit per-spawn override. Current RLM calls do not
    // expose one, so inherited parent tiers are replaced with the standard tier.
    if (hasExplicitFastMode(options)) return result;
    return { ...result, serviceTier: "default" };
  }

  Object.defineProperty(prototype, SPAWN_OPTIONS_METHOD, {
    ...descriptor,
    value: patchedSubagentRuntimeOptions,
  });
  Object.defineProperty(prototype, PATCH_STATE, {
    configurable: true,
    value: { descriptor },
  });
  return "installed";
}

/** Restore a prototype patched by this module. Intended for tests. */
export function restoreSubagentFastModeDefault(prototype) {
  const state = prototype?.[PATCH_STATE];
  if (!state) return false;
  Object.defineProperty(prototype, SPAWN_OPTIONS_METHOD, state.descriptor);
  delete prototype[PATCH_STATE];
  return true;
}
