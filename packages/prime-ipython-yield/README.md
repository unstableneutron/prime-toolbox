# prime-ipython-yield

Caps how long a single IPython cell may hold Prime Agent's only model-facing tool.

## The problem

`ipython` is the only tool the model can call, and it is backed by one
single-threaded IPython kernel. `KernelManager.execute()` resolves only when the
cell reaches iopub `status: idle`, so a blocking cell — `time.sleep(300)`, a slow
build in a `%%bash` cell, a wait loop for subagents — pins the entire agent for
its full duration. It cannot answer the user, and it cannot act on agent messages
that arrive in the meantime.

## What this does

A cell that runs longer than the yield cap (default 10s) is **detached**:

- the tool call returns with the output captured so far and a `cell_<n>` handle;
- the cell keeps running, completely untouched — nothing is interrupted;
- the model regains control to answer the user or absorb queued messages;
- when the cell finally settles, the session is **woken** with the outcome;
- `ipython_attach` collects output produced since the last look;
- `ipython_cancel` interrupts it (the same interrupt Ctrl+C sends).

While a cell is detached the kernel is genuinely busy, so new `ipython` calls are
**refused** with a status report rather than queued behind it — queueing would
block the new call for the detached cell's entire remaining lifetime, which is
the exact failure this package removes.

For work that should not block the kernel in the first place, use the `bg` skill
(`@unstableneutron/prime-bg-jobs`): it hands work to a subprocess or thread and
lets the kernel go idle immediately, so the model keeps its full tool surface.
This package is the backstop for when a cell blocks anyway.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PRIME_IPYTHON_YIELD_MS` | `10000` | Cap in ms, clamped to 1000–300000. `0` disables yielding entirely (stock behaviour). |
| `PRIME_IPYTHON_YIELD_MAX_CHARS` | `16000` | Cap on output text carried in a detach/attach report. |

## How it hooks in

Prime Agent has no extension hook for cell scheduling, so this patches two
narrow, public methods on live bundle instances. That is supported: the extension
loader serves pi packages from `VIRTUAL_MODULES` specifically so extensions share
the bundle's module instances.

- `IpythonKernelProvisioner.prototype.ensure` — the reachability hop.
  `KernelManager` is not exported from the package index, but every manager flows
  through `ensure()`, and patching a prototype reached from an instance
  intercepts all later calls.
- `KernelManager.prototype.execute` — where the wait is capped.

Both are public. No private method is patched, and the execution queue is never
reordered. If either method's shape changes in a future Prime Agent release the
patch fails loudly (`ensure`) or degrades to stock behaviour with one logged
error (`execute`).

Startup traffic is deliberately excluded: a manager only becomes eligible for
yielding after `ensure()` has fully resolved for it, which is after the kernel
bootstrap cell has run. Snapshot, restore and namespace-listing cells are marked
`internal` and always pass through unchanged.

### Import note

Extensions running inside the esbuild CLI bundle resolve pi packages through
`VIRTUAL_MODULES`, which maps `@sinclair/typebox` but **not** bare `typebox`.
`tools.js` imports the former.

## Known limits

- A detached cell blocks new cells. Real concurrency needs the kernel's JEP-91
  subshells (ipykernel 7 supports them), which requires host-side wire-protocol
  work and belongs upstream, not in a monkeypatch.
- `ipython_cancel` relies on the kernel's SIGINT path, which reaches the main
  thread only. A cell that traps or ignores `KeyboardInterrupt` still needs a
  kernel restart.

## Development

```bash
npm run check -w @unstableneutron/prime-ipython-yield
npm run test  -w @unstableneutron/prime-ipython-yield
```
