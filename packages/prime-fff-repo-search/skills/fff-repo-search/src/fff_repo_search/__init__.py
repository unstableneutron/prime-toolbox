from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from contextlib import AsyncExitStack
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

__all__ = ["SearchResponse", "find_files", "grep", "run", "status"]

_DEFAULT_ENDPOINT = "http://127.0.0.1:4319/mcp"
_MAX_LIMIT = 50
_MAX_CONTEXT_LINES = 5
_MAX_PATTERNS = 20
_MAX_FILTERS = 30
_MAX_TEXT_LENGTH = 1_024
_MAX_RESULT_LINE_LENGTH = 2_000
_MAX_RESPONSE_CHARACTERS = 64_000
_START_TIMEOUT_SECONDS = 8.0
_COMPACT_ITEMS = 6
_MAX_COMPACT_CHARACTERS = 4_096
_MAX_PREVIEW_FIELD_LENGTH = 240
_REQUIRED_TOOLS = frozenset({"fff_find_files", "fff_grep"})
_WINDOWS_CREATE_NEW_PROCESS_GROUP = getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
)
_WINDOWS_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_start_lock = asyncio.Lock()


class _IncompatibleRouterError(RuntimeError):
    pass


def _preview(value: Any, *, use_repr: bool = False) -> str:
    text = repr(value) if use_repr else str(value)
    text = text.strip()
    if len(text) > _MAX_PREVIEW_FIELD_LENGTH:
        return text[: _MAX_PREVIEW_FIELD_LENGTH - 3] + "..."
    return text


class SearchResponse(dict[str, Any]):
    """Router JSON response with a bounded, agent-friendly representation."""

    def compact(self, max_items: int = _COMPACT_ITEMS) -> str:
        """Format a bounded summary while preserving full data in the mapping."""
        max_items = max(0, min(max_items, _MAX_LIMIT))
        items = self.get("items")
        items = items if isinstance(items, list) else []
        stats = self.get("stats")
        stats = stats if isinstance(stats, dict) else {}
        total = stats.get("total_count", stats.get("result_count", len(items)))
        header = (
            "SearchResponse("
            f"backend={_preview(self.get('backend_used'), use_repr=True)}, "
            f"base_path={_preview(self.get('base_path'), use_repr=True)}, "
            f"shown={len(items)}, total={_preview(total, use_repr=True)}, "
            f"fallback={bool(self.get('fallback_applied'))})"
        )
        lines = [header]
        for item in items[:max_items]:
            if not isinstance(item, dict):
                lines.append(f"- {_preview(item, use_repr=True)}")
                continue
            path = _preview(item.get("path", item.get("absolute_path", "?")))
            line = item.get("line")
            text = _preview(item.get("text", ""))
            location = f"{path}:{_preview(line)}" if line is not None else path
            lines.append(f"- {location}{': ' + text if text else ''}")
        if len(items) > max_items:
            lines.append(f"... {len(items) - max_items} more in response['items']")
        compact = "\n".join(lines)
        if len(compact) > _MAX_COMPACT_CHARACTERS:
            compact = compact[: _MAX_COMPACT_CHARACTERS - 3] + "..."
        return compact

    def __repr__(self) -> str:
        return self.compact()

    __str__ = __repr__


def _endpoint() -> str:
    return (
        os.environ.get("FFF_ROUTER_MCP_URL", _DEFAULT_ENDPOINT).strip()
        or _DEFAULT_ENDPOINT
    )


def _bounded_text(value: str, *, name: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL bytes")
    if len(value) > max_length:
        raise ValueError(f"{name} exceeds the {max_length}-character limit")
    return value


def _bounded_pattern(value: str) -> str:
    """Validate a pattern without changing significant edge whitespace."""
    if not isinstance(value, str):
        raise TypeError("patterns must be a string")
    if not value.strip():
        raise ValueError("patterns must not be empty or whitespace-only")
    if "\x00" in value:
        raise ValueError("patterns must not contain NUL bytes")
    if len(value) > _MAX_TEXT_LENGTH:
        raise ValueError(f"patterns exceeds the {_MAX_TEXT_LENGTH}-character limit")
    return value


def _bounded_int(value: int, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _resolve_one_within(value: str | os.PathLike[str]) -> str:
    raw = os.fspath(value)
    raw = _bounded_text(raw, name="within")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    path = Path(expanded)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"search scope does not exist: {path}")
    return str(path)


def _resolve_within(
    within: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
) -> str | list[str]:
    if isinstance(within, (str, os.PathLike)):
        return _resolve_one_within(within)
    values = [_resolve_one_within(value) for value in within]
    if not values:
        raise ValueError("within must contain at least one path")
    if len(values) > 10:
        raise ValueError("within supports at most 10 paths per call")
    return list(dict.fromkeys(values))


def _string_list(
    values: Sequence[str] | None,
    *,
    name: str,
    maximum: int = _MAX_FILTERS,
) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    result = [_bounded_text(value, name=name, max_length=512) for value in values]
    result = list(dict.fromkeys(result))
    if len(result) > maximum:
        raise ValueError(f"{name} supports at most {maximum} entries")
    return result or None


def _extensions(values: Sequence[str] | None) -> list[str] | None:
    result = _string_list(values, name="extensions")
    if result is None:
        return None
    normalized = [value.removeprefix(".") for value in result]
    if any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", value) for value in normalized
    ):
        raise ValueError(
            "extensions must be suffixes such as 'py' or '.py', without paths or globs"
        )
    return list(dict.fromkeys(normalized))


def _patterns(values: str | Sequence[str]) -> list[str]:
    if isinstance(values, str):
        values = [values]
    result = [_bounded_pattern(value) for value in values]
    result = list(dict.fromkeys(result))
    if not result:
        raise ValueError("patterns must contain at least one search term")
    if len(result) > _MAX_PATTERNS:
        raise ValueError(f"patterns supports at most {_MAX_PATTERNS} entries")
    return result


def _reject_wildcard_only_regex(patterns: Sequence[str], *, literal: bool) -> None:
    if literal:
        return
    wildcard_only = re.compile(
        r"^(?:[.^$]*(?:[.][*+?]|[*+])[.^$]*|[.^$\s]*|\.\*[+?]?|\.\+[?]?|[.*?])$"
    )
    for pattern in patterns:
        if wildcard_only.fullmatch(pattern.strip()):
            raise ValueError(
                f"regex {pattern!r} matches everything; provide a concrete substring "
                "or read the known file directly"
            )


def _is_loopback_endpoint(endpoint: str) -> bool:
    hostname = urlsplit(endpoint).hostname
    if hostname is None:
        return False
    hostname = hostname.rstrip(".")
    if hostname.casefold() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_tools(tools: Sequence[str]) -> list[str]:
    tools = list(tools)
    missing = sorted(_REQUIRED_TOOLS.difference(tools))
    if missing:
        raise _IncompatibleRouterError(
            "fff-routerd endpoint is missing required tools: " + ", ".join(missing)
        )
    return tools


def _daemon_spawn_options(platform: str = os.name) -> dict[str, Any]:
    options: dict[str, Any] = {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.DEVNULL,
    }
    if platform == "nt":
        options["creationflags"] = (
            _WINDOWS_CREATE_NEW_PROCESS_GROUP | _WINDOWS_DETACHED_PROCESS
        )
    else:
        options["start_new_session"] = True
        options["close_fds"] = True
    return options


async def _open_session(stack: AsyncExitStack) -> ClientSession:
    endpoint = _endpoint()
    # Ignore proxy variables for the machine-local daemon. Custom remote
    # endpoints retain normal proxy and environment CA behavior.
    client = await stack.enter_async_context(
        httpx.AsyncClient(
            trust_env=not _is_loopback_endpoint(endpoint),
            timeout=httpx.Timeout(60.0, connect=3.0),
        )
    )
    read, write, *_ = await stack.enter_async_context(
        streamable_http_client(endpoint, http_client=client)
    )
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def _list_tools_once() -> list[str]:
    async with AsyncExitStack() as stack:
        session = await _open_session(stack)
        response = await session.list_tools()
        return [tool.name for tool in response.tools]


async def _call_once(tool: str, arguments: dict[str, Any]) -> Any:
    async with AsyncExitStack() as stack:
        session = await _open_session(stack)
        return await session.call_tool(tool, arguments)


async def _ensure_daemon() -> None:
    async with _start_lock:
        try:
            _validate_tools(await _list_tools_once())
            return
        except _IncompatibleRouterError:
            raise
        except Exception as initial_error:  # noqa: BLE001 - transport errors vary by SDK
            last_error: Exception | None = initial_error

        if _endpoint() != _DEFAULT_ENDPOINT:
            raise RuntimeError(
                f"custom fff-routerd endpoint is unreachable: {_endpoint()}: {last_error}"
            )

        executable = shutil.which("fff-routerd")
        if executable is None:
            raise RuntimeError(
                "fff-routerd is unavailable; install it or set FFF_ROUTER_MCP_URL "
                "to a reachable router endpoint"
            )

        await asyncio.create_subprocess_exec(executable, **_daemon_spawn_options())

        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            try:
                _validate_tools(await _list_tools_once())
                return
            except _IncompatibleRouterError:
                raise
            except Exception as exc:  # noqa: BLE001 - transport errors vary by SDK
                last_error = exc
        raise RuntimeError(
            f"fff-routerd did not become ready at {_endpoint()} within "
            f"{_START_TIMEOUT_SECONDS:g}s: {last_error}"
        )


def _search_response(payload: dict[str, Any]) -> SearchResponse:
    """Bound fallback output that may not have FFF's native line-size limits."""
    items = payload.get("items")
    if not isinstance(items, list):
        return SearchResponse(payload)

    truncated = False
    original_count = len(items)
    bounded_items: list[Any] = []
    used_characters = 0
    for item in items:
        if isinstance(item, dict):
            for key in ("text",):
                value = item.get(key)
                if isinstance(value, str) and len(value) > _MAX_RESULT_LINE_LENGTH:
                    item[key] = value[: _MAX_RESULT_LINE_LENGTH - 3] + "..."
                    truncated = True
            for key in (
                "context_before",
                "context_after",
                "contextBefore",
                "contextAfter",
            ):
                value = item.get(key)
                if isinstance(value, list):
                    bounded_lines = []
                    for line in value:
                        if (
                            isinstance(line, str)
                            and len(line) > _MAX_RESULT_LINE_LENGTH
                        ):
                            line = line[: _MAX_RESULT_LINE_LENGTH - 3] + "..."
                            truncated = True
                        bounded_lines.append(line)
                    item[key] = bounded_lines
        item_characters = len(json.dumps(item, ensure_ascii=False, default=str))
        if used_characters + item_characters > _MAX_RESPONSE_CHARACTERS:
            truncated = True
            break
        bounded_items.append(item)
        used_characters += item_characters

    if len(bounded_items) != original_count:
        truncated = True
    if truncated:
        payload["items"] = bounded_items
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            stats = {}
            payload["stats"] = stats
        stats["client_truncated"] = True
        stats["client_original_result_count"] = original_count
        stats["client_returned_count"] = len(bounded_items)
    return SearchResponse(payload)


def _parse_result(result: Any) -> SearchResponse:
    if bool(getattr(result, "isError", False)):
        texts = [
            getattr(item, "text", "")
            for item in getattr(result, "content", [])
            if getattr(item, "text", None) is not None
        ]
        for text in texts:
            try:
                error = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(error, dict):
                code = error.get("code", "TOOL_ERROR")
                message = error.get("message", text)
                raise RuntimeError(f"fff-routerd {code}: {message}")  # noqa: TRY004
        raise RuntimeError("fff-routerd tool error: " + "\n".join(texts))

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return _search_response(structured)

    texts = [
        getattr(item, "text", "")
        for item in getattr(result, "content", [])
        if getattr(item, "text", None) is not None
    ]
    if not texts:
        return SearchResponse()

    for text in texts:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            if len(texts) > 1:
                payload.setdefault(
                    "_messages", [part for part in texts if part != text]
                )
            return _search_response(payload)
    return SearchResponse({"mode": "text", "text": "\n".join(texts)})


async def _call(tool: str, arguments: dict[str, Any]) -> SearchResponse:
    try:
        result = await _call_once(tool, arguments)
    except Exception:  # noqa: BLE001 - reconnect after any transport failure
        await _ensure_daemon()
        result = await _call_once(tool, arguments)
    return _parse_result(result)


async def find_files(
    query: str,
    *,
    within: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] = ".",
    extensions: Sequence[str] | None = None,
    exclude_paths: Sequence[str] | None = None,
    limit: int = 20,
) -> SearchResponse:
    """Fuzzy-search repository-relative file paths through shared fff-routerd.

    Args:
        query: Short filename, path, or topic query; usually one or two terms.
        within: Existing file/directory scope, relative to cwd or absolute.
        extensions: Optional suffixes such as ``["py", "pyi"]``.
        exclude_paths: Optional relative descendant paths or simple wildcards.
        limit: Maximum returned items, from 1 through 50.
    """
    arguments: dict[str, Any] = {
        "query": _bounded_text(query, name="query"),
        "within": _resolve_within(within),
        "limit": _bounded_int(limit, name="limit", minimum=1, maximum=_MAX_LIMIT),
        "cursor": None,
        "output_mode": "json",
    }
    if (value := _extensions(extensions)) is not None:
        arguments["extensions"] = value
    if (value := _string_list(exclude_paths, name="exclude_paths")) is not None:
        arguments["exclude_paths"] = value
    return await _call("fff_find_files", arguments)


async def grep(
    patterns: str | Sequence[str],
    *,
    within: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] = ".",
    literal: bool = True,
    glob: str | None = None,
    extensions: Sequence[str] | None = None,
    exclude_paths: Sequence[str] | None = None,
    context_lines: int = 0,
    limit: int = 20,
) -> SearchResponse:
    """Search repository contents using one or several OR-patterns.

    Args:
        patterns: One term or naming variants matched with OR semantics.
        within: Existing file/directory scope, relative to cwd or absolute.
        literal: Literal search by default; false enables single-line regex.
        glob: Optional include glob relative to ``within``.
        extensions: Optional suffixes such as ``["go"]``.
        exclude_paths: Optional relative descendant paths or simple wildcards.
        context_lines: Context before/after each hit, from 0 through 5.
        limit: Maximum returned hits, from 1 through 50.
    """
    if not isinstance(literal, bool):
        raise TypeError("literal must be a boolean")
    normalized_patterns = _patterns(patterns)
    _reject_wildcard_only_regex(normalized_patterns, literal=literal)
    arguments: dict[str, Any] = {
        "patterns": normalized_patterns,
        "literal": literal,
        "within": _resolve_within(within),
        "context_lines": _bounded_int(
            context_lines,
            name="context_lines",
            minimum=0,
            maximum=_MAX_CONTEXT_LINES,
        ),
        "limit": _bounded_int(limit, name="limit", minimum=1, maximum=_MAX_LIMIT),
        "cursor": None,
        "output_mode": "json",
    }
    if glob is not None:
        arguments["glob"] = _bounded_text(glob, name="glob", max_length=512)
    if (value := _extensions(extensions)) is not None:
        arguments["extensions"] = value
    if (value := _string_list(exclude_paths, name="exclude_paths")) is not None:
        arguments["exclude_paths"] = value
    return await _call("fff_grep", arguments)


async def run(
    query: str,
    *,
    operation: Literal["grep", "find"] = "grep",
    within: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] = ".",
    extensions: Sequence[str] | None = None,
    exclude_paths: Sequence[str] | None = None,
    limit: int = 20,
) -> SearchResponse:
    """Run a common literal content search or fuzzy path search.

    Use :func:`grep` directly for regexes, multiple OR-patterns, globs, or
    context lines. Use :func:`find_files` directly for path-specific options.
    """
    if operation == "find":
        return await find_files(
            query,
            within=within,
            extensions=extensions,
            exclude_paths=exclude_paths,
            limit=limit,
        )
    if operation != "grep":
        raise ValueError("operation must be 'grep' or 'find'")
    return await grep(
        query,
        within=within,
        extensions=extensions,
        exclude_paths=exclude_paths,
        limit=limit,
    )


async def status() -> dict[str, Any]:
    """Return endpoint reachability, latency, and exposed router tool names."""
    started = time.perf_counter()
    try:
        tools = _validate_tools(await _list_tools_once())
    except _IncompatibleRouterError:
        raise
    except Exception:  # noqa: BLE001 - reconnect after any transport failure
        await _ensure_daemon()
        tools = _validate_tools(await _list_tools_once())
    return {
        "endpoint": _endpoint(),
        "reachable": True,
        "latency_ms": round((time.perf_counter() - started) * 1_000, 3),
        "tools": tools,
    }
