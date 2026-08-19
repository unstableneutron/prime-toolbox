---
name: guarded-file-ops
description: Guarded, bounded, format-aware reading and version-checked mutation for local files and directories. Use whenever an agent needs to read or list unknown, large, Unicode-named, notebook, PDF, Office/OpenDocument, CSV, image, or potentially hostile paths, or to create, overwrite, or exactly edit a file. Prefer guarded_file_ops.read/write/edit over unrestricted pathlib, open, cat/head/tail, or ad hoc document converters.
license: MIT
compatibility: Python 3.11+. Native wheels for pdf-inspector and firecrawl-anydoc cover common Linux, macOS, and Windows CPython platforms.
---

# Guarded File Ops

Use the pre-imported synchronous `guarded_file_ops` module. Prefer its small
functional API for ordinary work:

- `read(path, ...)` — bounded read, directory listing, or document extraction;
- `write(path, content, ...)` — create, or replace with `expected=version`;
- `edit(path, old, new, expected=version)` — exact version-checked edit.

Use `help(guarded_file_ops.<function>)` for typed parameters. Start with:

```python
page = guarded_file_ops.read("report.pdf", offset=1, limit=200)
print(page)
```

A result is a normal mapping with attribute access and a compact IPython
representation. Check `result.ok` before consuming it. Important fields are:

- `status`, `ok`, `category`, `message`
- `canonical_path`, `format`, `kind`, `version`
- `content`, `start_offset`, `end_offset`, `next_offset`, `total_lines`
- `truncated`, `truncated_by`, `warnings`
- `recovery`, `suggestions`, `conversion`, `pdf`, `metadata`
- `repeated`, `unchanged`, `changed_since_last_read`

Offsets are 1-based source lines or directory entries. Continue only with the
returned offset:

```python
page = guarded_file_ops.read(path)
while page.next_offset is not None:
    page = guarded_file_ops.read(path, offset=page.next_offset)
```

Per-line clamping is intentionally lossy. The omitted suffix of an oversized
single line cannot be retrieved with another line offset; use a targeted search
tool for minified or bundled lines.

## Version-checked mutation

Successful regular-file reads return an immutable, path-bound `FileVersion`.
Pass it back when modifying an existing file:

```python
observed = guarded_file_ops.read("settings.toml")
if observed.ok and observed.version is not None:
    edited = guarded_file_ops.edit(
        observed.canonical_path,
        "enabled = false",
        "enabled = true",
        expected=observed.version,
    )
```

`edit()` requires exactly one literal match by default and refuses zero or
multiple matches. Use `all_matches=True` only deliberately.

`write()` creates a genuinely missing file without a version:

```python
created = guarded_file_ops.write("new.txt", "content
")
```

Replacing an existing file requires its observed version:

```python
observed = guarded_file_ops.read(path)
replaced = guarded_file_ops.write(
    observed.canonical_path,
    new_content,
    expected=observed.version,
)
```

Existing-file writes without `expected` are refused. Stale, deleted, or
wrong-path versions are refused without mutation. Successful mutations return
a new chainable `result.version`. Identical writes or edits return `unchanged`
without replacing the inode.

Mutation uses exact paths and never fuzzy recovery. If `read()` recovered a
misspelled or Unicode-variant path, mutate `observed.canonical_path`.

## Scoped operations

Use `FileOps` only when a task needs an isolated observation tracker, a root
boundary, custom limits, disabled FFF recovery, or read-only policy:

```python
files = guarded_file_ops.FileOps(
    root=repo,
    policy=guarded_file_ops.FileOpsPolicy(
        use_fff=False,
        allow_mutation=True,
    ),
)
page = files.read("docs/report.pdf")
```

Relative paths are rooted at `root`; absolute paths and recovered candidates
must resolve inside it. This is cooperative containment, not an adversarial OS
sandbox.

## When to use it

Prefer `guarded_file_ops.read()` for:

- unknown, generated, user-supplied, or potentially large files;
- directories, logs, CSV, minified source, and lockfiles;
- Jupyter notebooks, PDFs, and structured documents;
- Unicode path spelling differences or ambiguous paths;
- paths that might resolve to a symlink, FIFO, socket, or device.

Do not bypass a refusal with `Path.read_text()`, unrestricted `open().read()`,
`cat`, `head`, `tail`, or an ad hoc converter merely to evade safety or context
limits.

## Structured formats

- Jupyter: `.ipynb`
- PDF: `.pdf` through PDF Inspector, never Anydoc's PDF path
- Word: `.doc`, `.docx`, `.docm`
- PowerPoint: `.ppt`, `.pps`, `.pot`, `.pptx`, `.pptm`, `.ppsx`, `.ppsm`
- Excel: `.xls`, `.xlsx`, `.xlsm`, `.xlsb`
- OpenDocument: `.odt`, `.ods`, `.odp`
- Other: `.rtf`, `.epub`, `.csv`

Notebook rendering omits images, widgets, binary MIME payloads, data URIs, and
oversized execution output. PDF results include page-level extraction and OCR
diagnostics. Route only `result.pdf["pages_needing_ocr"]` through Prime's
vision/OCR path; this package never performs hosted OCR. Images return bounded
metadata and vision guidance rather than decoded text.

## Failures and guarantees

Operational failures are structured results. Common categories include
`not_found`, `outside_root`, `ambiguous_path`, `broken_symlink`,
`non_regular_file`, `invalid_encoding`, `unsupported`, `malformed`,
`encrypted`, `resource_limited`, `missing_dependency`, `stale_version`,
`version_path_mismatch`, and `multiple_matches`. Invalid programmer arguments
raise `TypeError` or `ValueError`.

The package rejects non-regular targets before parser input, verifies opened
file identity, bounds source and response resources, and uses same-directory
atomic replacement where the platform permits it. It does not monkey-patch
`open`, `io`, `os`, `pathlib`, IPython, or interpreter-global behavior. Shells,
native extensions, other processes, and arbitrary Python can bypass this
cooperative API. Ordinary filesystems also cannot provide a portable, fully
atomic compare-and-swap across the final version check and replacement.

`guarded_file_ops.DEFAULT_LIMITS` sets 2,000 response lines, 50 KiB of UTF-8
response content, 2,000 characters per source line, 50 MiB per structured
source, and five path suggestions. Use `ReadLimits(...)` or `FileOpsPolicy`
for narrower task-specific ceilings; do not raise them merely to dump more
content into model context.
