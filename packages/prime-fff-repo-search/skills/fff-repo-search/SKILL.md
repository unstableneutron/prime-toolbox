---
name: fff-repo-search
description: Fast, persistent local repository path and content search through the shared fff-routerd service. Prefer it over manual recursive Python scans for exploratory or repeated searches that find files, symbols, definitions, usages, naming variants, or text in local code repositories.
license: MIT
compatibility: Requires fff-routerd on PATH or FFF_ROUTER_MCP_URL pointing to a reachable router.
---

# FFF Repo Search

Use the shared machine-local `fff-routerd` index through the Python module
`fff_repo_search`. The router keeps per-Git-root search runtimes warm across
turns, kernels, and agents; each Python call may open a fresh HTTP MCP session
without rebuilding the repository index.

## Prerequisite

Install [`fff-routerd`](https://github.com/unstableneutron/fff-router) separately
and make it available on `PATH`, or set `FFF_ROUTER_MCP_URL` to a reachable
router endpoint. The skill auto-starts a local `fff-routerd` when the default
endpoint is unavailable; it does not bundle the daemon or FFF runtime.

A custom endpoint receives absolute local paths and search queries. Use only a
trusted endpoint, and prefer authenticated HTTPS for non-loopback services.
Proxy settings and environment CA bundles are honored for remote endpoints.

## API

Find paths by fuzzy name/topic:

```python
result = await fff_repo_search.find_files(
    "validate token",
    within="/path/to/repo",
    extensions=["go"],
    exclude_paths=["vendor"],
    limit=20,
)
```

Search content. Put spelling/case variants of the same identifier in one call
instead of issuing sequential greps:

```python
result = await fff_repo_search.grep(
    ["ValidateToken", "validate_token"],
    within="/path/to/repo",
    literal=True,
    extensions=["go"],
    exclude_paths=["vendor"],
    context_lines=1,
    limit=20,
)
```

The module itself defaults to content grep:

```python
result = await fff_repo_search("ValidateToken", within="/path/to/repo")
result = await fff_repo_search(
    "author token", operation="find", within="/path/to/repo"
)
```

Diagnostics:

```python
await fff_repo_search.status()
```

All search calls return a `SearchResponse`, which is a normal Python `dict`
with a compact bounded `repr`. Full results remain available through
`response["items"]`; use `response.compact(max_items=...)` for a controlled
preview. Common fields are `base_path`, `backend_used`, `fallback_applied`,
`stats`, and `items`. Grep items contain `path`, `absolute_path`, `line`, `text`,
and optional context arrays.

## Search policy

1. Scope `within` to the relevant Git repository or a narrower directory. Do
   not search a broad multi-repo parent when the target repository is known.
2. Use `grep` for one specific bare identifier or text. Use `find_files` for
   paths/modules; it fuzzy-matches the whole relative path, is not semantic or
   vector search, and queries should usually contain only one or two terms.
3. Combine only spelling/case/naming variants of the same concept into one
   `grep([...])` call. Its patterns use OR semantics; unrelated patterns add
   noise and should not be bundled merely to save a call.
4. After one or two searches, read the best file with a bounded `Path` slice;
   do not keep searching variants unnecessarily.
5. Keep `limit <= 50` and `context_lines <= 5` (the skill enforces these caps).
6. Use `literal=False` only for a necessary single-line regex. Obvious
   wildcard-only regexes are rejected; read a known file directly instead.
7. Keep `rg` for one-off exhaustive verification, custom `--no-ignore`
   behavior, or files beyond FFF's normal content limits.

`within` may be relative to the live kernel cwd, absolute, or a list of paths.
Scopes must resolve under one Git root (or a configured non-Git allowlist); all
entries in a list must share that routing root. The endpoint defaults to
`http://127.0.0.1:4319/mcp`; override it with `FFF_ROUTER_MCP_URL`. If the local
daemon is down, the skill starts `fff-routerd` once and retries.
