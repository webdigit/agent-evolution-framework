# Command reference

Global options precede the command:

```console
aef [--workspace PATH] [--json | --human] [--compact] COMMAND
```

`--compact` implies JSON and conflicts with `--human`. Automation should always
use `--json` explicitly. Diagnostics go to stderr; stdout contains one human
rendering or one `aef.cli/v1` JSON document.

## INIT

```console
aef init --role ROLE [--instance-id ID] [--created-at RFC3339] [--dry-run]
```

Creates the official AEF V1 project state. A new-workspace dry-run requires an
explicit `--instance-id` and `--created-at`. Reuse the same values when running
the real initialization so it applies the plan that was reviewed. AEF does not
generate temporary dry-run values. Replaying identical input returns
`NO_CHANGE`.

## AUDIT

```console
aef audit
```

Read-only validation of required state, including knowledge. Findings produce
exit code 1.

## RECORD

```console
aef record --recording FILE [--dry-run]
```

Persists one explicit declared-fact recording at
`.agent/records/<record_id>.json`. The file stores only what the document
declares: `record_id`, `recorded_at`, `declared_by`, the payload collections
(`context`, `actions`, `outcomes`, `incidents`, `evidence`), and optional
`external_metrics`. RECORD does not score, grant authority, or update career,
competency, knowledge, or evaluation state.

The input must be `aef.record.submit/v1` without a `digest`. AEF computes the
persisted `aef.record/v1` digest; a caller-supplied digest is rejected. See
[Canonical input files](input-files.md) for an executable example.

Replaying the same valid recording against an existing valid matching file
returns `NO_CHANGE` and does not rewrite any byte. Reusing the same
`record_id` with different content is blocked without rewriting. An existing
file that is unreadable, malformed, or whose digest no longer matches its body
is also blocked without writing.

`--dry-run` renders the exact plan and creates neither the records directory
nor the record file. AUDIT inspects `.agent/records/` when that directory
exists; absence is not an error. A symlink, malformed record, or digest
mismatch is reported as a finding.

## DISCOVER

```console
aef discover --snapshot connectors.json [--dry-run]
```

Reconciles an explicit strict-JSON connector snapshot into the registry.
Discovery records capabilities but grants no authority.

## CONSOLIDATE

```console
aef consolidate --reviews reviews.json [--dry-run]
```

Reviews existing rule lifecycles with `keep`, `specialize`, `supersede`, or
`retire`. Modifying actions require explicit human approval and resolvable
evidence. V1 does not create principles or autonomous knowledge.

## EVALUATE

```console
aef evaluate --list
aef evaluate --decisions decisions.json [--dry-run]
aef evaluate --refresh [--dry-run]
aef evaluate --recover [--dry-run]
```

`--list` is strictly read-only. Explicit decisions recalculate readiness and
promotions advance at most one level. `--refresh` can modify recommendation
statuses. `--recover` can modify several files while finalizing or rolling back
an interrupted transaction; never invoke it without an explicit human request.
Begin refresh and recovery with `--dry-run`. Required recovery blocks every
other modifying operation. See [Canonical input files](input-files.md) and
[EVALUATE recovery](recovery.md).

## Claude integration

```console
aef integrate claude [--scope project] [--dry-run]
aef integrate claude --status [--scope project]
aef integrate claude --remove [--dry-run] [--scope project]
```

Only project scope exists in V1.

## UPGRADE

```console
aef upgrade --check
aef upgrade --dry-run
aef upgrade
aef upgrade --recover --dry-run
aef upgrade --recover
```

Upgrade migrates the **workspace** `.agent/` toward the contract supported by
the **already installed** package. It is not a software update. There is no
`aef update` command and no `--target-schema` flag.

`--check` shows the plan without writing. `--dry-run` computes the projected
result without creating a file or directory. `upgrade` applies the plan.
`--recover` handles only an unfinished UPGRADE transaction.

On a valid V1 workspace already at `schema_version` `1.0.0`, `--check`,
`--dry-run`, `upgrade`, and replay return `NO_CHANGE`.

The envelope stays `aef.cli/v1` with `command=UPGRADE`. Human and JSON output
carry the same decision. `init`, `record`, `evaluate`, and `audit` do not
trigger an upgrade.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success, including RECORD `CHANGE` and `NO_CHANGE` |
| 1 | AUDIT found problems |
| 2 | Invalid command-line arguments |
| 3 | Invalid input document or unsupported option, including an invalid `--recording` file |
| 4 | Operation blocked without mutation, including a RECORD conflict or an unreadable existing record |
| 5 | Business `FAILED`, including a declared UPGRADE `MigrationFailure` |
| 6 | Filesystem or permission failure |
| 70 | Unexpected internal failure |

Run `aef --help` and `aef COMMAND --help` for the authoritative installed
syntax.
