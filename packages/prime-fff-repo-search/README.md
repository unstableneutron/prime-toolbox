# prime-fff-repo-search

A Python-backed Prime Agent skill for fast, persistent repository path and
content search through a shared [`fff-routerd`](https://github.com/unstableneutron/fff-router)
daemon.

The package owns only the Prime-facing client and search guidance. The daemon,
FFF runtimes, backend selection, indexes, and lifecycle management remain in
`fff-router` so other agents and tools can share the same warm service.

## Prerequisite

Provide either:

- `fff-routerd` on `PATH`; or
- `FFF_ROUTER_MCP_URL` pointing to a reachable router endpoint.

At the default local endpoint, the skill starts `fff-routerd` automatically
when needed. A custom unreachable endpoint fails without launching a local
daemon. Custom endpoints receive absolute local paths and search queries, so
use only trusted services and prefer authenticated HTTPS outside loopback.
Remote endpoints retain normal proxy and environment CA behavior.

## Usage

```python
await fff_repo_search.find_files("auth service", within="~/src/project")
await fff_repo_search.grep(
    ["ValidateToken", "validate_token"],
    within="~/src/project",
    extensions=["go"],
    exclude_paths=["vendor"],
)
await fff_repo_search.status()
```

Search results are ordinary dictionaries wrapped in `SearchResponse` for a
bounded, agent-friendly representation.

## Test

```bash
npm test --workspace @unstableneutron/prime-fff-repo-search
npm run test:live --workspace @unstableneutron/prime-fff-repo-search
npm run check --workspace @unstableneutron/prime-fff-repo-search
```
