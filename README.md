# prime-toolbox

Portable skills and extensions for [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent).
The repository is a single installable Prime Agent package and a workspace for independently reusable subpackages.

## Packages

- [`prime-websearch-parallel`](packages/prime-websearch-parallel): overrides the bundled `websearch` Python skill with Serper and Parallel Search API support.
- [`prime-fff-repo-search`](packages/prime-fff-repo-search): provides fast repository path and content search through a shared `fff-routerd` service.
- [`prime-guarded-file-ops`](packages/prime-guarded-file-ops): provides guarded, bounded, format-aware local file operations, structured-document extraction, change tracking, and stale-aware atomic mutation.
- [`prime-subagent-fast-mode`](packages/prime-subagent-fast-mode): defaults RLM subagents to Fast mode off instead of inheriting the parent's service tier.
- [`prime-bg-jobs`](packages/prime-bg-jobs): provides the `bg` skill for running shell commands and Python callables in the background so a long task never blocks the single-threaded IPython kernel.
- [`prime-ipython-yield`](packages/prime-ipython-yield): caps how long one IPython cell may hold the agent's only tool, detaching long cells so the model regains control, with `ipython_attach` / `ipython_cancel` to recover them.

## Install

From this checkout:

```bash
prime-agent package install ~/Projects/prime-toolbox
```

From Git after publishing the repository:

```bash
prime-agent package install git:github.com/unstableneutron/prime-toolbox
```

Prime Agent packages deliberately use the inherited `pi` manifest key. The root manifest exposes resources from the subpackages, so installing this repository once loads the complete toolbox.

Because the toolbox intentionally replaces the bundled `websearch`, disable only that bundled skill in `~/.prime/agent/settings.json` to avoid an expected collision diagnostic:

```json
{
  "bundledSkills": {
    "websearch": false
  }
}
```

The packaged `websearch` remains enabled; this setting only removes Prime's lower-precedence bundled copy.

The FFF search skill requires `fff-routerd` on `PATH` or a reachable endpoint in `FFF_ROUTER_MCP_URL`. The router remains a separate machine-wide service so Prime Agent and other clients share the same warm repository indexes.

Credentials are never stored in this repository. See each subpackage for setup.

## Development

```bash
npm test
npm run check
```

The websearch subpackage intentionally uses the bundled skill's Python distribution name, `prime-agent-skill-websearch`. This lets Prime's editable installation replace the bundled module rather than leaving two competing `websearch` imports in the kernel environment.
