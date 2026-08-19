---
name: bg
description: Run shell commands, builds, dev servers, and heavy Python callables in the background instead of blocking the agent's single IPython kernel. Use bg.run/bg.call whenever a cell would take more than a few seconds - installs, builds, test suites, servers, downloads, long loops - then bg.tail/bg.result on a later turn. Prefer bg over inline subprocess.run, os.system, time.sleep, %%bash long-command, or any wait-until-done loop.
license: MIT
compatibility: Python 3.11+, pure standard library. Process groups and signals require POSIX; process spawning works anywhere subprocess does.
---

# bg — background jobs

Prime Agent has one model-facing tool (`ipython`) backed by **one single-threaded
kernel**. While a cell runs, that cell *is* the agent: it cannot answer the user,
receive agent messages, or start anything else. `time.sleep(300)`,
`subprocess.run(["pnpm", "build"])`, and `%%bash pnpm test` therefore cost the
agent its entire tool surface for their whole duration.

Handing the work to a subprocess or a thread returns in milliseconds and the
kernel goes idle immediately, while the work keeps running. `bg` is that escape
hatch, productized: spooled output, stdin, bounded waits, process-group kill,
and metadata that survives a kernel restart.

**Rule of thumb: if a cell might take longer than a few seconds, it belongs in `bg`.**

```python
job = bg.run("pnpm -w build")     # returns instantly
bg.result(job)                    # -> {'state': 'running', ...}
# ... end your turn ...
bg.tail(job)["text"]              # next turn: read what happened
```

## API

| Call | Purpose |
| --- | --- |
| `bg.run(command, *, cwd=None, env=None, shell=True, label=None)` | Start a shell/argv command in its own process group; returns a `Job` at once. |
| `bg.call(fn, *args, label=None, **kwargs)` | Run a Python callable on a daemon thread; captures return value and exception. |
| `bg.list(include_done=True)` | Rows of `id, label, kind, state, elapsed, exit_code, out_bytes`. |
| `bg.tail(job, *, since=0, max_chars=8000)` | Incremental output: `{text, offset, eof, truncated, state}`. |
| `bg.write(job, data, *, close=False)` | Write to a `run()` job's stdin; returns bytes written. |
| `bg.wait(job, timeout=5.0)` | **Bounded** wait, capped at 60s. `state="running"` is a normal answer. |
| `bg.kill(job, *, sig=SIGTERM, escalate_after=5.0)` | Signal the whole process group, escalating to `SIGKILL`. |
| `bg.result(job)` | Snapshot without waiting: state, exit code, `value`, `error`. |
| `bg.clean(*, keep_running=True)` | Drop finished jobs and delete their spool files. |

Every function accepts a `Job` object **or** a job id string, so a plain
`"sh3-9f2a1c"` from an earlier turn keeps working.

States: `running`, `done` (exit 0), `failed` (non-zero exit or raised
exception), `killed`, `orphaned` (spooled by a previous kernel, process gone).

## Do NOT poll in a loop

`bg.wait(job)` returning `state="running"` is **success, not an error**. It
means "still working". The correct follow-up is:

> **spawn → end your turn → check on the next turn.**

Never write `while not done: bg.wait(...)`, never `time.sleep()` until a job
finishes, and never re-issue `bg.wait` back-to-back. That reconstructs exactly
the blocking cell `bg` exists to remove. `wait()` is capped at 60 seconds and
`kill()`'s grace period at 10, so even a mistaken call returns control.

If nothing else will wake you, schedule a wake-up and end the turn:

```python
await rlm_heartbeat.create("check bg jobs", interval="30s")
job = bg.run("pnpm -w test")
```

Then, on a later turn, read the delta and stop the heartbeat when the work is
finished.

## Long shell build

```python
build = bg.run("pnpm -w build", cwd="/repo", label="build")
# ... later turn ...
page = bg.tail(build)                      # {'text': ..., 'offset': 5120, 'eof': False}
snapshot = bg.result(build)                # {'state': 'running'|'done'|'failed', 'exit_code': ...}
later = bg.tail(build, since=page["offset"])   # only what is new
```

Keep `page["offset"]` in a kernel variable; passing it back as `since` means two
tails never re-read the same bytes and long logs never flood the context.

## Dev server plus tail

```python
server = bg.run("pnpm dev --port 5173", cwd="/repo", label="dev-server")
bg.tail(server)["text"]          # check for "ready in ..." on a later turn
bg.kill(server)                  # SIGTERM to the whole group, SIGKILL after 5s
```

Because the child gets its own session, `bg.kill` reaches the node/vite/esbuild
children too - not just the shell that launched them.

## Interactive prompt answered with bg.write

```python
job = bg.run("npm login")
bg.tail(job)["text"]             # -> "Username: "
bg.write(job, "ada\n")           # include the newline the program waits for
bg.write(job, "hunter2\n")
bg.write(job, "", close=True)    # send EOF when the program expects it
```

## Heavy in-kernel Python

```python
def reindex(paths: list[str]) -> dict:
    ...  # minutes of work

job = bg.call(reindex, paths, label="reindex")
# ... later turn ...
done = bg.result(job)
if done["state"] == "done":
    index = done["value"]
elif done["state"] == "failed":
    print(done["error"]["traceback"])
```

`call()` jobs cannot be killed (Python threads are not interruptible) and pure
Python code still competes for the GIL, so prefer `bg.run` for genuinely
CPU-bound work. `call()` is right for I/O-bound work and for code that must
share the kernel's live objects.

## Spool and restarts

Output and metadata live in `$PRIME_BG_DIR`, else
`<tempdir>/prime-bg-jobs/<kernel-pid>/`, created lazily. Each job writes
`<id>.out` (stdout and stderr merged) and `<id>.json`. Output goes to files, not
pipes, so a chatty job can never deadlock on a full pipe buffer.

After a kernel restart `bg.list()` rescans the spool and re-adopts what it
finds; jobs whose process is gone are reported as `orphaned`. Reattached jobs
can still be tailed and killed, but not written to. Run `bg.clean()` when a
batch of work is finished.
