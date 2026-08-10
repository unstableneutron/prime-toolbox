---
name: robust-read
description: Bounded, format-aware reading for unknown, large, Unicode-named, notebook, PDF, Office, OpenDocument, RTF, EPUB, CSV, image, or potentially hostile local paths. Use before Path.read_text(), unrestricted open().read(), cat, head, tail, or ad hoc document converters when file size/type/safety is not already known.
license: MIT
compatibility: Python 3.11+. Native wheels for pdf-inspector and firecrawl-anydoc cover common Linux, macOS, and Windows CPython platforms.
---

# Robust Read

Use the pre-imported `prime_robust_read` module from Prime's IPython kernel.
It is synchronous:

```python
result = prime_robust_read.read("report.pdf", offset=1, limit=200)
print(result)
```

`result` is a normal mapping with a compact, bounded IPython representation.
The current bounded window is in `result["content"]`; do not expect `repr()` to
contain the entire window. Important fields include:

- `status`, `category`, `message`
- `format`, `kind`, `canonical_path`
- `start_offset`, `end_offset`, `next_offset`, `total_lines`
- `truncated`, `truncated_by`, `warnings`
- `recovery`, `suggestions`, `conversion`, `pdf`, `metadata`
- `repeated`, `unchanged`, `changed_since_last_read`

Offsets are 1-based source lines. When `next_offset` is present, continue with
that exact value instead of rereading the whole file:

```python
page = prime_robust_read.read(path)
while page.next_offset is not None:
    page = prime_robust_read.read(path, offset=page.next_offset)
```

The per-line character ceiling deliberately discards the remainder of an
oversized single line. That omitted remainder is not addressable by a later
line offset; the warning says so explicitly. Use a targeted search tool for a
minified/bundled line instead of trying to evade this limit.

## When to use it

Prefer `prime_robust_read.read()` for:

- an unknown file or a file that may be large;
- generated logs, CSV, minified source, lockfiles, or hostile inputs;
- `.ipynb` notebooks;
- PDFs and structured documents;
- user-supplied paths whose Unicode spelling may differ from disk;
- any path that might be a symlink, FIFO, socket, or device.

Do not bypass it with `Path.read_text()`, unrestricted `open().read()`, shell
`cat`/`head`/`tail`, or an ad hoc converter merely to avoid its output or
safety limits.

## Supported structured extensions

- Jupyter: `.ipynb`
- PDF: `.pdf` (directly through PDF Inspector, never Anydoc's PDF path)
- Word: `.doc`, `.docx`, `.docm`
- PowerPoint: `.ppt`, `.pps`, `.pot`, `.pptx`, `.pptm`, `.ppsx`, `.ppsm`
- Excel: `.xls`, `.xlsx`, `.xlsm`, `.xlsb`
- OpenDocument: `.odt`, `.ods`, `.odp`
- Other: `.rtf`, `.epub`, `.csv`

Notebook rendering preserves Markdown, fenced code, and bounded textual
outputs. It strips ANSI/progress redraws, compresses errors, and omits images,
widgets, binary MIME payloads, and oversized execution output. Treat those
omissions as intentional.

PDF Inspector returns local text plus classification, confidence, page count,
table/column/encoding diagnostics, and exact locally detected OCR pages.
Extracted PDF text may be incomplete. If `result["pdf"]["pages_needing_ocr"]`
or its ranges are non-empty, route only those pages through Prime's available
vision/OCR path. Never claim a scanned or flagged page was extracted when its
page block says it was not. This package does not run hosted OCR.

Image files return safe metadata and vision guidance; their bytes are never
decoded as text.

## Recovery and failures

The reader tries the exact path, then bounded sibling matching across NFC/NFD
and common quote/dash/space equivalents. It recovers automatically only from
one safe match and returns bounded suggestions for ambiguity. When installed,
the sibling `fff_repo_search` package is an optional broader resolver. A
missing client or unreachable `fff-routerd` only disables broad fuzzy recovery;
ordinary reading still works.

Check `category` for `not_found`, `ambiguous_path`, `broken_symlink`,
`non_regular_file`, `invalid_encoding`, `unsupported`, `malformed`,
`encrypted`, `resource_limited`, `missing_dependency`, or
`conversion_failed`. Do not feed a refused non-regular path to another generic
reader or converter.

## Read-before-write and stale-read checks

Use the same ledger explicitly when isolation matters:

```python
ledger = prime_robust_read.ReadLedger()
observed = prime_robust_read.read("settings.toml", ledger=ledger)
edited = prime_robust_read.safe_edit(
    "settings.toml",
    "enabled = false",
    "enabled = true",
    ledger=ledger,
)
created = prime_robust_read.safe_write(
    "new.txt", "content\n", require_read=True, ledger=ledger
)
```

`safe_edit()` requires a prior successful read by default. `safe_write()` can
optionally require one and always allows a genuinely new file. Both refuse a
known stale identity and use same-directory atomic replacement where the
platform permits it.

The guarantee applies only to operations routed through `safe_write()` and
`safe_edit()`. Arbitrary Python, shell, IPython, editor, or other-process writes
can bypass the ledger, and there is still a small unavoidable check/replace
race on ordinary filesystems.

## Default ceilings

`prime_robust_read.DEFAULT_LIMITS` sets 2,000 response lines, 50 KiB of UTF-8
response bytes, 2,000 characters per source line, 50 MiB per structured source,
and five path suggestions. Pass a validated `ReadLimits(...)` for a narrower
task-specific ceiling; do not raise limits simply to dump more content into
the model context.
