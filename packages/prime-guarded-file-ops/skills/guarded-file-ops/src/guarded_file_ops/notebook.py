"""Bounded native Jupyter notebook rendering.

The output handling is derived in spirit from Hermes Agent's hardened
``read_extract`` notebook renderer and LobeHub PR #17855. The implementation
here is adapted to Prime's Python/IPython result model and MIT-licensed; see
the package's THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .limits import ReadLimits

_CSI = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)")
_ESCAPE = re.compile(r"\x1b(?:[()][0-2A-Z]|[@-_])|[\x07\x9c]")
_PROGRESS = re.compile(
    r"^\s*(?:\d{1,3}%\|.*\||\d+\s*/\s*\d+\s*\[).*?(?:it/s|s/it|ETA|<).*?$",
    re.IGNORECASE,
)
_LANGUAGE = re.compile(r"[A-Za-z0-9_+.-]+")
_BACKTICKS = re.compile(r"`+")
_DATA_URI = re.compile(
    r"data:(image/[A-Za-z0-9.+-]+|audio/[A-Za-z0-9.+-]+);base64,"
    r"([A-Za-z0-9+/=\r\n]{4,})",
    re.IGNORECASE,
)


class NotebookError(Exception):
    pass


def _source(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(item for item in value if isinstance(item, str))
    return ""


def _clean_terminal(text: str) -> str:
    cleaned = _OSC.sub("", text)
    cleaned = _CSI.sub("", cleaned)
    cleaned = _ESCAPE.sub("", cleaned).replace("\r\n", "\n")
    rendered: list[str] = []
    for line in cleaned.split("\n"):
        frames = [frame for frame in line.split("\r") if frame]
        visible = frames[-1] if frames else ""
        if not _PROGRESS.match(visible):
            rendered.append(visible)
    return "\n".join(rendered)


def _decoded_size(value: Any) -> int:
    payload = re.sub(r"[^A-Za-z0-9+/=]", "", _source(value))
    padding = min(2, len(payload) - len(payload.rstrip("=")))
    return max(0, len(payload) * 3 // 4 - padding)


def _fence(source: str) -> str:
    maximum = max((len(match.group(0)) for match in _BACKTICKS.finditer(source)), default=0)
    return "`" * max(3, maximum + 1)


def _scrub_data_uris(text: str) -> tuple[str, int]:
    omitted = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal omitted
        omitted += 1
        size = _decoded_size(match.group(2))
        return f"[{match.group(1).lower()} embedded data omitted: {size:,} bytes]"

    return _DATA_URI.sub(replace, text), omitted


def _output_text(output: Any) -> tuple[str, dict[str, int]]:
    omitted = {"assets": 0, "widgets": 0, "binary_outputs": 0}
    if not isinstance(output, dict):
        return "", omitted
    output_type = output.get("output_type")
    if output_type == "stream":
        return _clean_terminal(_source(output.get("text"))), omitted
    if output_type in {"error", "pyerr"}:
        name = str(output.get("ename") or "Error")
        value = _clean_terminal(str(output.get("evalue") or ""))
        return f"Error: {name}{': ' + value if value else ''}", omitted
    if output_type not in {"execute_result", "display_data", "pyout"}:
        return "", omitted

    data = output.get("data")
    if not isinstance(data, dict):
        data = {}
        if isinstance(output.get("text"), (str, list)):
            data["text/plain"] = output["text"]
        for legacy, mime in (("png", "image/png"), ("jpeg", "image/jpeg")):
            if legacy in output:
                data[mime] = output[legacy]

    if "application/vnd.jupyter.widget-view+json" in data:
        omitted["widgets"] += 1
        return "[interactive widget omitted]", omitted
    for mime, value in data.items():
        if isinstance(mime, str) and mime.startswith("image/"):
            omitted["assets"] += 1
            return f"[{mime} output omitted: {_decoded_size(value):,} bytes]", omitted
    for mime in ("text/markdown", "text/plain"):
        body = _clean_terminal(_source(data.get(mime)))
        if body.strip():
            body, data_uris = _scrub_data_uris(body)
            omitted["assets"] += data_uris
            return body, omitted
    omitted["binary_outputs"] += 1
    names = ", ".join(str(name) for name in data) or "unknown"
    return f"[{names} output omitted]", omitted


def render_notebook(data: bytes, limits: ReadLimits) -> tuple[str, dict[str, Any]]:
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise NotebookError(f"Notebook is not valid UTF-8: {exc}") from exc
    try:
        notebook = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NotebookError(f"Malformed notebook JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(notebook, dict):
        raise NotebookError("Notebook root must be a JSON object")

    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise NotebookError("Notebook has no valid cells array")
    metadata = notebook.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    language_info = metadata.get("language_info")
    language_info = language_info if isinstance(language_info, dict) else {}
    kernelspec = metadata.get("kernelspec")
    kernelspec = kernelspec if isinstance(kernelspec, dict) else {}
    raw_language = language_info.get("name") or kernelspec.get("language") or ""
    match = _LANGUAGE.search(str(raw_language))
    language = match.group(0).lower() if match else ""

    diagnostics: dict[str, Any] = {
        "cell_counts": {"markdown": 0, "code": 0, "raw": 0, "unknown": 0},
        "omitted_assets": 0,
        "omitted_widgets": 0,
        "omitted_binary_outputs": 0,
        "omitted_attachments": 0,
        "truncated_outputs": 0,
        "language": language or None,
    }
    rendered: list[str] = []
    for index, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            diagnostics["cell_counts"]["unknown"] += 1
            continue
        cell_type = cell.get("cell_type")
        source = _source(cell.get("source"))
        source, source_assets = _scrub_data_uris(source)
        diagnostics["omitted_assets"] += source_assets
        attachments = cell.get("attachments")
        if isinstance(attachments, dict):
            diagnostics["omitted_attachments"] += len(attachments)
            diagnostics["omitted_assets"] += len(attachments)
        if cell_type == "markdown":
            diagnostics["cell_counts"]["markdown"] += 1
            rendered.extend((f"<!-- Markdown cell {index} -->", source.rstrip(), ""))
        elif cell_type == "code":
            diagnostics["cell_counts"]["code"] += 1
            fence = _fence(source)
            rendered.extend(
                (f"<!-- Code cell {index} -->", f"{fence}{language}", source.rstrip(), fence)
            )
            outputs = cell.get("outputs")
            outputs = outputs if isinstance(outputs, list) else []
            output_blocks: list[str] = []
            for output in outputs:
                body, omitted = _output_text(output)
                for key, value in omitted.items():
                    diagnostics[f"omitted_{key}"] += value
                if body.strip():
                    output_blocks.append(body.rstrip())
            joined = "\n".join(output_blocks)
            if len(joined) > limits.max_notebook_output_characters:
                omitted_characters = len(joined) - limits.max_notebook_output_characters
                joined = (
                    joined[: limits.max_notebook_output_characters]
                    + f"\n[... {omitted_characters:,} output characters omitted]"
                )
                diagnostics["truncated_outputs"] += 1
            if joined:
                rendered.extend(("", f"<!-- Output for cell {index} -->", joined))
            rendered.append("")
        elif cell_type == "raw":
            diagnostics["cell_counts"]["raw"] += 1
            fence = _fence(source)
            rendered.extend((f"<!-- Raw cell {index} -->", fence, source.rstrip(), fence, ""))
        else:
            diagnostics["cell_counts"]["unknown"] += 1
            if source:
                fence = _fence(source)
                rendered.extend(
                    (
                        f"<!-- Unknown cell type {cell_type!r}, cell {index} -->",
                        fence,
                        source.rstrip(),
                        fence,
                        "",
                    )
                )

    if not rendered:
        raise NotebookError("Notebook contains no readable cells")
    return "\n".join(rendered).rstrip() + "\n", diagnostics


__all__ = ["NotebookError", "render_notebook"]
