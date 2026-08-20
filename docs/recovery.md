# EVALUATE recovery

EVALUATE can update evaluations plus career or competency state. A project-local
journal at `.agent/state/evaluation-transaction.json` protects that multi-file
change from interruption.

When a journal is present or its filesystem entry cannot be inspected safely,
modifying operations are blocked. Do not edit or delete the journal manually.

## Inspect without writing

```console
aef audit
aef evaluate --recover --dry-run
```

AUDIT reports `evaluation-recovery-required`. The dry-run shows the official
recovery result without changing files.

## Recover

```console
aef evaluate --recover
aef audit
```

AEF validates the transaction identity, journal content, paths, and exact
before/after documents before resuming or rolling back. Successful recovery
removes the journal last. Repeating recovery after completion is safe.

If recovery is blocked, preserve the complete `.agent/` directory and report
the problem; do not fabricate a replacement journal. Per-file writes elsewhere
are atomic, but they are not a general transaction across all `.agent/` files.

## UPGRADE recovery

UPGRADE can change several managed files. A project-local journal at
`.agent/state/upgrade-transaction.json` (`aef.upgrade-transaction/v1`)
protects that mutation.

This journal is **distinct** from `.agent/state/evaluation-transaction.json`.
Do not confuse them. Do not edit or delete a journal by hand.

### Inspect without writing

```console
aef audit
aef upgrade --recover --dry-run
```

AUDIT reports `upgrade-recovery-required` (distinct from
`evaluation-recovery-required`). While an UPGRADE transaction is unfinished,
`upgrade`, `--check`, and `--dry-run` return `BLOCKED`. Only `--recover`
handles it.

### Recover

```console
aef upgrade --recover
aef audit
```

`prepared` and compatible before-states roll back. `committed` with exact
after-states finalizes. An invalid journal or divergent hash is `BLOCKED`
without writing. Repeating recover after success is safe.
