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
