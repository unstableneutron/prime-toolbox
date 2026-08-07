---
name: websearch
description: Search the web through Serper or the Parallel Search API. Use for current information, source discovery, web research, and factual lookups. Configure Serper through /login or provide PARALLEL_API_KEY.
license: MIT
---

# Web Search

Use the prepared `websearch` Python import:

```python
print(await websearch("latest Prime Agent release"))
```

The default provider is `auto`: Serper is preferred when configured, otherwise Parallel is used. Override it per call or with `PRIME_AGENT_WEBSEARCH_PROVIDER`:

```python
print(await websearch("latest AI news", provider="parallel"))
```

Parallel accepts an optional natural-language objective and up to three keyword queries:

```python
print(await websearch(
    "new database releases",
    provider="parallel",
    objective="Find official announcements for database releases from the last month",
    search_queries=["database release announcement", "new database version"],
))
```

## Setup

- Parallel: provide `PARALLEL_API_KEY`. The default mode is `turbo`; override it with `PRIME_AGENT_WEBSEARCH_PARALLEL_MODE=turbo|basic|advanced`.
- Serper: run `/login`, switch to **MCP Connections**, and choose **Serper (web search)**.
- Common overrides: `PRIME_AGENT_WEBSEARCH_TIMEOUT` and `PRIME_AGENT_WEBSEARCH_NUM_RESULTS`.

If neither provider is configured, explain these setup choices to the user. Never request that credentials be committed to the toolbox repository.
