# prime-websearch-parallel

A Python-backed Prime Agent skill that overrides the bundled `websearch` skill while preserving its callable API. It supports:

- Serper Google Search
- Parallel GA Search (`POST /v1/search`)

## Configuration

Disable Prime's bundled copy in `~/.prime/agent/settings.json`; the packaged override remains enabled and the expected collision warning disappears:

```json
{
  "bundledSkills": {
    "websearch": false
  }
}
```

Set the desired provider explicitly when needed:

```bash
export PRIME_AGENT_WEBSEARCH_PROVIDER=parallel  # auto, serper, or parallel
export PARALLEL_API_KEY=...
```

`auto` is the default. It preserves existing behavior by selecting Serper when both credentials exist, otherwise it selects the only configured provider.

Parallel defaults to `turbo` mode, matching the low-latency behavior of the reference Pi extension. Override it when higher retrieval quality is preferred:

```bash
export PRIME_AGENT_WEBSEARCH_PARALLEL_MODE=advanced  # turbo, basic, or advanced
export PRIME_AGENT_WEBSEARCH_MAX_CHARS_TOTAL=20000
```

The existing settings remain supported:

```bash
export PRIME_AGENT_WEBSEARCH_TIMEOUT=45
export PRIME_AGENT_WEBSEARCH_NUM_RESULTS=5
```

Serper can still be configured through Prime Agent's `/login` → **MCP Connections** → **Serper (web search)** flow. Until Prime Agent adds a Parallel service credential, prefer `PARALLEL_API_KEY`. A manually stored `auth.json` entry named `parallel` may contain a literal key or environment-variable reference; command references such as `!security ...` require host integration and are not executed by this package.

## Usage

```python
await websearch("latest Prime Agent release")
await websearch(
    "Prime Agent release notes",
    provider="parallel",
    objective="Find the latest official Prime Agent release notes",
    search_queries=["Prime Agent release notes", "Prime Agent changelog"],
)
```

## Test

```bash
cd skills/websearch
uv run python -m unittest discover -s tests -v
```
