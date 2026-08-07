# prime-subagent-fast-mode

A Prime Agent extension that makes **Fast mode off** the default for RLM subagents, instead of inheriting the parent session's Fast mode.

## Behavior

Given a parent running with Fast mode on (`serviceTier: "priority"`), a normal `rlm(...)` spawn receives:

```js
{ serviceTier: "default" }
```

The parent remains unchanged. The extension also preserves any future explicit per-spawn `serviceTier` or `fastMode` option rather than overriding it.

## Why this is an extension patch

Prime Agent 0.7 constructs RLM spawn options in the private runtime method `AgentSession._createRlmSubagentRuntimeOptions()` and currently exposes no extension event for modifying them. This extension patches only that method, installs idempotently, and throws a clear compatibility error if a future Prime Agent release removes or changes it.

Disable the extension through Prime's package resource configuration if a later release adds a native subagent service-tier setting.

## Install

Install the top-level toolbox once:

```bash
prime-agent package install ~/Projects/prime-toolbox
```

Then start a fresh Prime Agent session or run `/reload` before spawning new subagents.

## Test

```bash
npm test --workspace @unstableneutron/prime-subagent-fast-mode
```
