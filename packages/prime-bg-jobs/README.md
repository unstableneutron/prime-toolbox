# prime-bg-jobs

`bg` is a Python-backed Prime Agent skill for running work outside the agent's
single-threaded IPython kernel. Prime Agent's only model-facing tool is that
kernel, so any blocking cell - `time.sleep`, `subprocess.run`, `%%bash pnpm
build` - pins the agent's entire tool surface until it finishes. `bg` hands the
work to a subprocess or a daemon thread, returns in milliseconds, and lets the
kernel go idle while the work continues.

## Install

Install the toolbox once from its repository root:

```bash
prime-agent package install ~/Projects/prime-toolbox
```

Prime discovers the skill's `pyproject.toml` and installs it editable into the
kernel environment, exposing the `bg` import. The package is pure standard
library and requires Python 3.11+.

## API

```python
job = bg.run("pnpm -w build", cwd="/repo", label="build")   # returns instantly
bg.list()                       # id, label, kind, state, elapsed, exit_code, out_bytes
page = bg.tail(job)             # {'text': ..., 'offset': 5120, 'eof': False}
bg.tail(job, since=page["offset"])                          # only new bytes
bg.write(job, "yes\n")          # stdin for run() jobs
bg.wait(job, timeout=5.0)       # bounded, capped at 60 seconds
bg.kill(job)                    # SIGTERM to the process group, SIGKILL after 5s
bg.result(job)                  # state, exit_code, value, error
bg.clean()                      # drop finished jobs and their spool files

heavy = bg.call(reindex, paths, label="reindex")             # daemon thread
bg.result(heavy)["value"]
```

Every function accepts a `Job` or a job id string.

## Guarantees

- **Nothing blocks unboundedly.** `wait()` is capped at 60 seconds and
  `kill(escalate_after=...)` at 10. `result()` and `tail()` never wait at all.
  `wait()` returning `state="running"` is the normal outcome; the caller should
  end its turn and check back, not loop.
- **Output is spooled to files**, not in-memory pipes, so a chatty job cannot
  deadlock on a full pipe buffer and logs survive a kernel restart.
- **Process-group isolation.** `run()` uses `start_new_session=True`, so
  `kill()` reaches the whole child tree, not just the launching shell.
- **Restart-visible.** Each job mirrors metadata to `<id>.json` in
  `$PRIME_BG_DIR` (else `<tempdir>/prime-bg-jobs/<pid>/`). A fresh kernel
  rescans that spool; jobs whose process is gone become `orphaned`.
- **Thread-safe registry**, pure standard library, no dependencies.

`call()` jobs run on daemon threads: they capture the return value and the
exception with its traceback, but cannot be killed and still contend for the
GIL. Use `run()` for CPU-bound work.

## Development

```bash
cd packages/prime-bg-jobs
npm run check
npm run test
```

Tests use sub-second sleeps, no network, and an isolated `PRIME_BG_DIR` per
test.
