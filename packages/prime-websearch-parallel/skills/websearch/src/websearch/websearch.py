"""Serper and Parallel web search implementation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import httpx

Provider = Literal["auto", "serper", "parallel"]
ParallelMode = Literal["turbo", "basic", "advanced"]

_PROVIDER_NAMES = {"serper": "Serper", "parallel": "Parallel"}
_CREDENTIALS = {
    "serper": ("SERPER_API_KEY", "serper"),
    "parallel": ("PARALLEL_API_KEY", "parallel"),
}
_VALID_PROVIDERS = frozenset({"auto", *_CREDENTIALS})
_VALID_PARALLEL_MODES = frozenset({"turbo", "basic", "advanced"})


def _env_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back on invalid values."""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _agent_dir() -> Path:
    """Resolve the Prime Agent config directory the same way as the runtime."""
    raw = (
        os.environ.get("PRIME_AGENT_CODING_AGENT_DIR")
        or os.environ.get("PI_CODING_AGENT_DIR")
        or str(Path.home() / ".prime" / "agent")
    )
    return Path(raw).expanduser()


def _resolve_config_value(value: str) -> str:
    """Resolve a literal or environment-variable reference from auth.json."""
    value = value.strip()
    if not value or value.startswith("!"):
        return ""
    return (os.environ.get(value) or value).strip()


def _stored_api_key(credential_id: str) -> str:
    try:
        auth = json.loads((_agent_dir() / "auth.json").read_text())
        credential = auth.get(credential_id) if isinstance(auth, dict) else None
        if isinstance(credential, dict) and credential.get("type") == "api_key":
            return _resolve_config_value(str(credential.get("key") or ""))
    except (OSError, ValueError):
        pass
    return ""


def _resolve_api_key(provider: str) -> str:
    env_name, credential_id = _CREDENTIALS[provider]
    return os.environ.get(env_name, "").strip() or _stored_api_key(credential_id)


def _select_provider(requested: str | None) -> tuple[str, str]:
    provider = (
        (requested or os.environ.get("PRIME_AGENT_WEBSEARCH_PROVIDER") or "auto")
        .strip()
        .lower()
    )
    if provider not in _VALID_PROVIDERS:
        choices = ", ".join(sorted(_VALID_PROVIDERS))
        raise ValueError(
            f"Unknown web search provider {provider!r}; expected one of: {choices}"
        )

    if provider != "auto":
        return provider, _resolve_api_key(provider)

    # Preserve the bundled skill's behavior when both providers are configured.
    for candidate in ("serper", "parallel"):
        api_key = _resolve_api_key(candidate)
        if api_key:
            return candidate, api_key
    return "auto", ""


def _bounded_num_results(value: int) -> int:
    return max(1, min(20, value))


def _parallel_mode(value: str | None) -> str:
    mode = (
        (value or os.environ.get("PRIME_AGENT_WEBSEARCH_PARALLEL_MODE") or "turbo")
        .strip()
        .lower()
    )
    if mode not in _VALID_PARALLEL_MODES:
        choices = ", ".join(sorted(_VALID_PARALLEL_MODES))
        raise ValueError(
            f"Unknown Parallel search mode {mode!r}; expected one of: {choices}"
        )
    return mode


def _format_serper_results(data: dict, query: str, num_results: int) -> str:
    sections: list[str] = []

    knowledge_graph = data.get("knowledgeGraph")
    if isinstance(knowledge_graph, dict):
        lines: list[str] = []
        title = str(knowledge_graph.get("title") or "").strip()
        if title:
            lines.append(f"Knowledge Graph: {title}")
        description = str(knowledge_graph.get("description") or "").strip()
        if description:
            lines.append(description)
        attributes = knowledge_graph.get("attributes") or {}
        if isinstance(attributes, dict):
            for key, value in attributes.items():
                text = str(value).strip()
                if text:
                    lines.append(f"{key}: {text}")
        if lines:
            sections.append("\n".join(lines))

    organic = data.get("organic") or []
    for index, result in enumerate(organic[:num_results]):
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "").strip() or "Untitled"
        lines = [f"Result {index}: {title}"]
        link = str(result.get("link") or "").strip()
        if link:
            lines.append(f"URL: {link}")
        snippet = str(result.get("snippet") or "").strip()
        if snippet:
            lines.append(snippet)
        sections.append("\n".join(lines))

    questions: list[str] = []
    for item in (data.get("peopleAlsoAsk") or [])[:3]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        entry = f"Q: {question}"
        answer = str(item.get("snippet") or "").strip()
        if answer:
            entry += f"\nA: {answer}"
        questions.append(entry)
    if questions:
        sections.append("People Also Ask:\n" + "\n".join(questions))

    return (
        "\n\n---\n\n".join(sections)
        if sections
        else f"No results returned for query: {query}"
    )


def _format_parallel_results(data: dict, query: str, num_results: int) -> str:
    sections: list[str] = []
    results = data.get("results") or []

    for index, result in enumerate(results[:num_results]):
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "").strip() or "Untitled"
        lines = [f"Result {index}: {title}"]
        url = str(result.get("url") or "").strip()
        if url:
            lines.append(f"URL: {url}")
        publish_date = str(result.get("publish_date") or "").strip()
        if publish_date:
            lines.append(f"Published: {publish_date}")
        excerpts = result.get("excerpts") or []
        if isinstance(excerpts, list):
            lines.extend(
                excerpt.strip()
                for excerpt in excerpts
                if isinstance(excerpt, str) and excerpt.strip()
            )
        sections.append("\n".join(lines))

    warnings = data.get("warnings") or []
    if warnings:
        warning_lines = [str(item).strip() for item in warnings if str(item).strip()]
        if warning_lines:
            sections.append(
                "Warnings:\n" + "\n".join(f"- {item}" for item in warning_lines)
            )

    return (
        "\n\n---\n\n".join(sections)
        if sections
        else f"No results returned for query: {query}"
    )


def _redact_header_secrets(text: str, headers: dict[str, str]) -> str:
    for name, value in headers.items():
        if name.lower() in {"authorization", "x-api-key"} and value:
            text = text.replace(value, "<redacted>")
    return text


async def _post_json(
    url: str,
    *,
    payload: dict,
    headers: dict[str, str],
    timeout: int,
    provider_name: str,
    client: httpx.AsyncClient | None = None,
) -> dict:
    async def send(active_client: httpx.AsyncClient) -> dict:
        response = await active_client.post(url, json=payload, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = _redact_header_secrets(response.text[:2000], headers)
            raise RuntimeError(
                f"{provider_name} search error ({response.status_code}): {body}"
            ) from exc
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError(
                f"{provider_name} search returned a non-object JSON response"
            )
        return data

    if client is not None:
        return await send(client)
    async with httpx.AsyncClient(timeout=timeout) as active_client:
        return await send(active_client)


async def _fetch_serper(
    query: str,
    api_key: str,
    *,
    timeout: int,
    num_results: int,
    client: httpx.AsyncClient | None = None,
) -> str:
    data = await _post_json(
        "https://google.serper.dev/search",
        payload={"q": query},
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        timeout=timeout,
        provider_name="Serper",
        client=client,
    )
    return _format_serper_results(data, query, num_results)


async def _fetch_parallel(
    query: str,
    api_key: str,
    *,
    objective: str | None,
    search_queries: list[str] | None,
    mode: str,
    timeout: int,
    num_results: int,
    max_chars_total: int,
    client: httpx.AsyncClient | None = None,
) -> str:
    queries = [item.strip() for item in (search_queries or [query]) if item.strip()]
    if not queries:
        raise ValueError("Parallel search requires at least one non-empty search query")
    if len(queries) > 3:
        raise ValueError(
            f"Parallel search accepts at most 3 search queries; received {len(queries)}"
        )

    excerpt_chars = max(1000, min(5000, max_chars_total // num_results))
    normalized_objective = (
        objective.strip() if objective and objective.strip() else query
    )
    payload = {
        "objective": normalized_objective,
        "search_queries": queries,
        "mode": mode,
        "max_chars_total": max_chars_total,
        "advanced_settings": {
            "max_results": num_results,
            "excerpt_settings": {"max_chars_per_result": excerpt_chars},
        },
    }
    base_url = os.environ.get("PARALLEL_BASE_URL", "https://api.parallel.ai").rstrip(
        "/"
    )
    data = await _post_json(
        f"{base_url}/v1/search",
        payload=payload,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        timeout=timeout,
        provider_name="Parallel",
        client=client,
    )
    return _format_parallel_results(data, query, num_results)


def _missing_key_message(provider: str) -> str:
    if provider == "serper":
        return (
            "Web search is not set up yet: no Serper API key is configured.\n"
            'Run /login, switch to MCP Connections, choose "Serper (web search)", and paste the key.'
        )
    if provider == "parallel":
        return (
            "Web search is not set up yet: no Parallel API key is configured.\n"
            "Configure PARALLEL_API_KEY, then retry the search."
        )
    return (
        "Web search is not set up yet: neither Serper nor Parallel is configured.\n"
        "Choose one setup path:\n"
        '  - Serper: run /login, switch to MCP Connections, and choose "Serper (web search)".\n'
        "  - Parallel: configure PARALLEL_API_KEY."
    )


def _truncate(output: str, max_output: int) -> str:
    if len(output) <= max_output:
        return output
    total = len(output)
    marker = f"\n... [output truncated, {total} chars total] ...\n"
    half = max(0, (max_output - len(marker)) // 2)
    truncated = output[:half] + marker + output[total - half :]
    return truncated[:max_output]


async def run(
    query: str,
    *,
    provider: Provider | None = None,
    max_output: int = 8192,
    timeout: int | None = None,
    num_results: int | None = None,
    objective: str | None = None,
    search_queries: list[str] | None = None,
    parallel_mode: ParallelMode | None = None,
) -> str:
    """Search the web through Serper or Parallel and return formatted results.

    Args:
        query: Search query or question.
        provider: Provider selection. Auto prefers Serper when both are configured.
        max_output: Maximum number of returned characters.
        timeout: HTTP timeout in seconds.
        num_results: Number of results to request, clamped to 1-20.
        objective: Optional Parallel natural-language search objective.
        search_queries: Optional list of up to three Parallel keyword queries.
        parallel_mode: Parallel mode: turbo, basic, or advanced.

    Returns:
        Formatted titles, URLs, dates, snippets, and excerpts.
    """
    query = query.strip()
    if not query:
        return "Web search requires a non-empty query."
    max_output = max(0, max_output)

    try:
        selected_provider, api_key = _select_provider(provider)
    except ValueError as exc:
        return f"Web search configuration error: {exc}"
    if not api_key:
        return _missing_key_message(selected_provider)

    timeout = (
        timeout
        if timeout is not None
        else _env_int("PRIME_AGENT_WEBSEARCH_TIMEOUT", 45)
    )
    requested_results = (
        num_results
        if num_results is not None
        else _env_int("PRIME_AGENT_WEBSEARCH_NUM_RESULTS", 5)
    )
    result_count = _bounded_num_results(requested_results)

    try:
        if selected_provider == "serper":
            result = await _fetch_serper(
                query,
                api_key,
                timeout=timeout,
                num_results=result_count,
            )
        else:
            mode = _parallel_mode(parallel_mode)
            max_chars_total = max(
                1000,
                min(50000, _env_int("PRIME_AGENT_WEBSEARCH_MAX_CHARS_TOTAL", 20000)),
            )
            result = await _fetch_parallel(
                query,
                api_key,
                objective=objective,
                search_queries=search_queries,
                mode=mode,
                timeout=timeout,
                num_results=result_count,
                max_chars_total=max_chars_total,
            )
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError) as exc:
        result = f"Error searching for {query!r} with {_PROVIDER_NAMES[selected_provider]}: {exc}"

    return _truncate(f'Results for query "{query}":\n\n{result}', max_output)
