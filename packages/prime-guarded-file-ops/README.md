# prime-guarded-file-ops

`guarded_file_ops` is a Python-backed Prime Agent skill for guarded, bounded,
format-aware local file operations. It rejects special files before I/O,
incrementally reads UTF-8 text, renders notebooks natively, extracts structured
documents, tracks observed file identities, and provides stale-aware atomic
writes and exact edits.

## Install

Install the toolbox once from its repository root:

```bash
prime-agent package install ~/Projects/prime-toolbox
```

Prime discovers the skill's `pyproject.toml` and installs it editable into the
kernel environment. The skill and Python distribution are named
`guarded-file-ops`; Prime exposes the contract-required `guarded_file_ops`
import in its IPython kernel.

The pinned native dependencies are:

- `pdf-inspector==0.2.6` (CPython 3.8+ wheels for Linux x86-64/aarch64,
  macOS Intel/Apple Silicon, and Windows x64; other targets require Rust);
- `firecrawl-anydoc==0.1.7` (CPython 3.10+ abi3 wheels for Linux
  x86-64/aarch64 glibc and musl, macOS Intel/Apple Silicon, and Windows x64;
  other targets build from source with Rust).

Both imports are lazy. A missing or broken native module yields a categorized
document failure and never prevents ordinary text/source reading after the
base package is importable.

## API

The module-level functions are the canonical API:

```python
import guarded_file_ops

result = guarded_file_ops.read("report.pdf", offset=1, limit=200)
print(result)
print(result["pdf"])

if result.next_offset is not None:
    next_page = guarded_file_ops.read(
        "report.pdf", offset=result.next_offset, limit=200
    )
```

`ReadResult` and `MutationResult` subclass `dict`, support attribute access,
and keep `repr()`/`str()` below 4 KiB. Operational failures are structured
results with `ok=False`; invalid programmer arguments raise `TypeError` or
`ValueError`.

Offsets are 1-based source-line numbers. `next_offset` always names the next
unreturned source line. Per-line clamping is lossy by design: the remainder of
one overlong line is not exposed through another line offset and is reported
in `warnings`.

For isolated state, root containment, or custom policy, use the secondary API:

```python
files = guarded_file_ops.FileOps(
    root=repo,
    policy=guarded_file_ops.FileOpsPolicy(
        limits=guarded_file_ops.ReadLimits(max_lines=500),
        use_fff=False,
        allow_mutation=True,
    ),
)
result = files.read("docs/report.pdf")
```

## Supported formats

| Family | Extensions | Renderer |
| --- | --- | --- |
| Text/source | any non-binary extension | incremental strict UTF-8 |
| Jupyter | `.ipynb` | native bounded renderer |
| PDF | `.pdf` | PDF Inspector |
| Word | `.doc`, `.docx`, `.docm` | Anydoc |
| PowerPoint | `.ppt`, `.pps`, `.pot`, `.pptx`, `.pptm`, `.ppsx`, `.ppsm` | Anydoc |
| Excel | `.xls`, `.xlsx`, `.xlsm`, `.xlsb` | Anydoc |
| OpenDocument | `.odt`, `.ods`, `.odp` | Anydoc |
| Other | `.rtf`, `.epub`, `.csv` | Anydoc |

Known image signatures and image extensions return metadata plus guidance to
Prime's vision path. Image bytes are not decoded as text.

## Limits and memory behavior

The centralized `ReadLimits` defaults are:

- 2,000 response lines;
- 50 KiB of UTF-8 response content;
- 2,000 characters retained per source line;
- 50 MiB maximum structured-document source;
- five path suggestions.

Text is read in fixed chunks with a strict incremental UTF-8 decoder. A
pathological 50 MiB single line retains only one chunk plus the configured
line prefix; normal text memory does not scale with total file size. Native
document APIs are whole-file converters, so only structured sources buffer up
to the explicit 50 MiB ceiling. Rendered Markdown is then paginated through
the same byte, line, and per-line budgets as text.

Directories use a bounded filesystem-order scan and return the same pagination
fields. Empty files and offsets at or beyond EOF have explicit statuses.

## Path and file safety

The final symlink target is resolved and statted. A descriptor is opened with
`O_NONBLOCK`, `O_CLOEXEC`, and `O_NOFOLLOW` where available, then `fstat()` is
checked before any bytes reach a parser or converter. FIFOs, sockets,
character/block devices, and all other non-regular file targets are rejected.
The descriptor identity is compared with the pre-open identity and with the
post-read metadata to catch practical path/open and concurrent-write races.

Missing paths first get a bounded sibling scan using NFC normalization and
known straight/curly quote, dash, and Unicode-space equivalents. Exactly one
safe match may be recovered. Ambiguity returns at most five suggestions and is
never guessed. If `fff_repo_search` is importable, it is tried as a broader
Git-repository resolver in a bounded daemon thread; the reader remains fully
functional without the client or `fff-routerd`.

Invalid UTF-8, binary NUL content, malformed/encrypted/unsupported documents,
native dependency failures, and Anydoc resource-limit failures retain distinct
categories in the result.

## Notebook and PDF limitations

The notebook renderer keeps Markdown/raw/code cell source and bounded textual
outputs. It strips ANSI escapes and carriage-return progress rewrites, reduces
execution errors to `ename: evalue`, widens code fences safely, and omits
embedded base64 images, widgets, binary MIME payloads, and oversized output.
Omission counts are under `result["metadata"]["notebook"]`.

PDF Inspector performs local text-layer extraction, classification, and layout
diagnosis without OCR. `result["pdf"]` includes confidence, page count,
per-page extraction diagnostics, table/column pages, encoding issues, and
pages/reasons requiring OCR. Page Markdown is rendered with page headers into
the normal bounded `content` stream. Scanned or unreliable pages carry an
explicit placeholder; hosted OCR is deliberately excluded. Send only flagged
pages through Prime's available vision/OCR path.

## Version-checked mutation

Every successful regular-file read returns an immutable, path-bound
`FileVersion`. Existing-file mutations require that version:

```python
observed = guarded_file_ops.read("config.toml")
assert observed.ok and observed.version is not None
edited = guarded_file_ops.edit(
    observed.canonical_path,
    "old",
    "new",
    expected=observed.version,
)
assert edited.ok and edited.version is not None
replaced = guarded_file_ops.write(
    observed.canonical_path,
    "complete replacement\n",
    expected=edited.version,
)
created = guarded_file_ops.write("created.txt", "hello\n")
```

`edit()` requires exactly one literal match by default; `all_matches=True`
enables an explicit replace-all. Existing-file `write()` calls without
`expected` are refused. Stale, deleted, and wrong-path tokens are refused.
Successful mutations return a new chainable version, while identical content
returns `unchanged` without replacing the inode.

Writes use a same-directory temporary file, flush/fsync, mode preservation for
existing files, and atomic replacement where supported. This is a cooperative
boundary, not global interception. Arbitrary Python, shell, IPython, editor,
native extension, or other-process operations can bypass it, and ordinary
filesystems cannot provide a portable fully atomic compare-and-swap across the
last version check and replacement. The package never monkey-patches `open()`,
`io`, `os`, `pathlib.Path`, builtins, IPython, or interpreter-global behavior.

## Development

```bash
cd packages/prime-guarded-file-ops
aube test
aube run check
aube run typecheck
aube run build
```

Tests generate large/transient inputs at runtime. Small Anydoc fixtures retain
their upstream provenance in `THIRD_PARTY_NOTICES.md`.
