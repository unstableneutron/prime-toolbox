import assert from "node:assert/strict";
import test from "node:test";

import {
  installSubagentFastModeDefaultOff,
  PATCH_STATE,
  restoreSubagentFastModeDefault,
} from "../extensions/subagent-fast-mode/patch.js";

function createPrototype() {
  return {
    _createRlmSubagentRuntimeOptions(options) {
      return {
        id: options.id,
        model: options.model,
        serviceTier: "priority",
      };
    },
  };
}

test("defaults inherited subagent service tier to standard", () => {
  const prototype = createPrototype();
  assert.equal(installSubagentFastModeDefaultOff(prototype), "installed");

  const result = prototype._createRlmSubagentRuntimeOptions({
    id: "child-1",
    model: "test-model",
  });

  assert.deepEqual(result, {
    id: "child-1",
    model: "test-model",
    serviceTier: "default",
  });
});

test("preserves explicit future service-tier overrides", () => {
  const prototype = createPrototype();
  installSubagentFastModeDefaultOff(prototype);

  const result = prototype._createRlmSubagentRuntimeOptions({
    id: "child-1",
    serviceTier: "priority",
  });

  assert.equal(result.serviceTier, "priority");
});

test("preserves explicit future fast-mode overrides", () => {
  const prototype = createPrototype();
  installSubagentFastModeDefaultOff(prototype);

  const result = prototype._createRlmSubagentRuntimeOptions({
    id: "child-1",
    fastMode: true,
  });

  assert.equal(result.serviceTier, "priority");
});

test("installs idempotently and restores the original method", () => {
  const prototype = createPrototype();
  const original = prototype._createRlmSubagentRuntimeOptions;

  assert.equal(installSubagentFastModeDefaultOff(prototype), "installed");
  const patched = prototype._createRlmSubagentRuntimeOptions;
  assert.notEqual(patched, original);
  assert.ok(prototype[PATCH_STATE]);

  assert.equal(
    installSubagentFastModeDefaultOff(prototype),
    "already-installed",
  );
  assert.equal(prototype._createRlmSubagentRuntimeOptions, patched);

  assert.equal(restoreSubagentFastModeDefault(prototype), true);
  assert.equal(prototype._createRlmSubagentRuntimeOptions, original);
  assert.equal(prototype[PATCH_STATE], undefined);
});

test("fails loudly when the Prime Agent runtime method is unavailable", () => {
  assert.throws(
    () => installSubagentFastModeDefaultOff({}),
    /Unsupported Prime Agent version/,
  );
});
