# Command reference

Global options precede the command:

```console
aef [--workspace PATH] [--json | --human] [--compact] COMMAND
```

`--compact` implies JSON and conflicts with `--human`. Automation should always
use `--json` explicitly. Diagnostics go to stderr; stdout contains one human
rendering or one `aef.cli/v1` JSON document.

The local pytest suite invokes `python -m aef`; the published console script is
smoke-tested in CI via `tests/test_console_script_entry.py` (WDAC-blocked hosts
skip that test with an explicit reason).

## INIT

```console
aef init --role ROLE [--instance-id ID] [--created-at RFC3339] [--dry-run]
```

Creates the official project state under the V1 workspace contract
(`schema_version` `1.0.0`). A new-workspace dry-run requires an
explicit `--instance-id` and `--created-at`. Reuse the same values when running
the real initialization so it applies the plan that was reviewed. AEF does not
generate temporary dry-run values. Replaying identical input returns
`NO_CHANGE`.

## AUDIT

```console
aef audit
```

Read-only validation of required state, including knowledge. Findings produce
exit code 1. After an ingest, AUDIT also checks record ↔ knowledge provenance.
A workspace that never ingested remains valid. After a competency declaration,
AUDIT checks declaration provenance; a competency present without a birth
event is a non-blocking warning. An empty `competencies.json` (`{}`) is not a
finding. AUDIT does not inspect managed guidance segments in `AGENTS.md`,
`CLAUDE.md`, or `GEMINI.md`. Hand-edits of those blocks are reported by
`aef integrate … --status` as `BLOCKED`, not as AUDIT findings.

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

RECORD is a journal only. It does not derive signals. Use `ingest` after the
record exists. There is no `record --learn` flag.

## INGEST

```console
aef ingest --intake FILE [--dry-run]
aef --json ingest --intake FILE [--dry-run]
```

Cite persisted `record_id` values and declare already-normalized learning
events. AEF calls the existing `ingest_events` engine and writes only
`.agent/knowledge/knowledge.json`. It does not rewrite records, create XP,
rules, principles, or competencies, and it is not `doctor` or `upgrade`.

`--json` is global and must precede the command. `--dry-run` projects the
knowledge change without writing. Replaying the same intake against the same
knowledge returns `NO_CHANGE`. A missing record or a digest that does not
match the persisted file is `BLOCKED` (exit 4) with no write. An invalid
intake is `ERROR` (exit 3).

See [Canonical input files](input-files.md) for an executable example.

## Guidance integration

```console
aef integrate agents [--status|--remove] [--dry-run] [--scope project]
aef integrate claude [--status|--remove] [--dry-run] [--scope project]
aef integrate gemini [--status|--remove] [--dry-run] [--scope project]
aef integrate runtime [--status|--remove] [--dry-run] [--scope project]
aef integrate all [--status|--remove] [--dry-run] [--scope project]
aef --json integrate claude --status
```

Install project-local guidance doors. `AGENTS.md` holds the only managed copy
of readable rules (citations to `.agent/core/` and `docs/runtime.md`).
`CLAUDE.md` and `GEMINI.md` are doorbells without doctrine rules.
`integrate runtime` produces `docs/runtime.md` as a pure snapshot of
`aef doctor` (divergence is périmé/stale, never catalog tampering); `doctor`
remains read-only.
`integrate claude` remains stable; it installs the root doorbell and does not
create a new `.claude/CLAUDE.md` bridge. An existing brownfield bridge is
reported by `--status` and never rewritten silently.

`integrate all` is atomic across doors: if any requested door is `BLOCKED`,
no door is written (exit 4). A quoted marker inside a markdown fence or a
four-space indented block is not a marker; documenting a doorbell in a project's
own README-style fence is the nominal case and must not classify as installed
or be stripped by `--remove`.

Guidance only — not permission, hooks, or host settings. See
[Claude and agent guidance](claude-integration.md).

## COMPETENCY DECLARE

```console
aef competency declare --declaration FILE [--dry-run]
aef --json competency declare --declaration FILE [--dry-run]
aef competency declare --recover [--dry-run]
```

Declare the official birth of a competency at **L1** only. The document must
cite at least one persisted `record_id` with a matching digest and include an
explicit human decision (`source=human`, `approved=true`). AEF writes
`.agent/state/competencies.json` and the provenance ledger
`.agent/state/competency-declarations.json`. It does not rewrite records,
grant XP, Trust, or permissions, and it is not `evaluate`, `ingest`, or
`doctor`.

`--json` is global and must precede the command. `--dry-run` projects the L1
entry without writing. Replaying the same declaration returns `NO_CHANGE`. A
missing record, digest mismatch, or id collision is `BLOCKED` (exit 4) with no
write. An invalid document or missing human decision is `ERROR` (exit 3).

Interrupted applies leave a distinct journal
`.agent/state/competency-declaration-transaction.json`. Recover it with
`--recover` (start with `--dry-run`). While that journal exists, other
mutations are blocked. AUDIT reports brownfield competencies without a
declaration event as a non-blocking warning; it never invents provenance.

See [Canonical input files](input-files.md) for an executable example.

## LEARNING VALIDATE

```console
aef learning validate --validation FILE [--dry-run]
aef --json learning validate --validation FILE [--dry-run]
```

Apply explicit human validation to one or more **candidate** hypotheses cited
by derived id (`hypothesis:…`), and/or promote **active** rules to principles
by citing derived `rule:…` ids. AEF sets `explicit_human_validation: true` on
hypotheses (without changing `confirmations`), derives rules when the hypothesis
gate opens, and derives principles only when `rules` are cited with a human
`decision` block. It does not ingest events, promote competencies, or invoke
EVALUATE.

`--json` is global and must precede the command. `--dry-run` projects the exact
knowledge change without writing. Replaying the same validation returns
`NO_CHANGE`. A missing hypothesis, a hypothesis already promoted to a rule, a
missing cited record, or a digest mismatch is `BLOCKED` (exit 4) with no write.
An invalid document or missing human decision is `ERROR` (exit 3).

See [Canonical input files](input-files.md) for an executable example.

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
evidence. The V1 workspace contract does not create principles or autonomous
knowledge.

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

On a valid V1 workspace contract already at `schema_version` `1.0.0`, `--check`,
`--dry-run`, `upgrade`, and replay return `NO_CHANGE`.

The envelope stays `aef.cli/v1` with `command=UPGRADE`. Human and JSON output
carry the same decision. `init`, `record`, `evaluate`, and `audit` do not
trigger an upgrade.

## DOCTOR

```console
aef doctor
aef --json doctor
```

Read-only runtime diagnosis. `doctor` does not write under `.agent/`, does not
modify an existing virtual environment, and does not run `pip` or create
environments. It never executes third-party binaries from `PATH`. Use `--json`
before the command.

A compatible runtime returns `PASS` (exit 0). A missing or incompatible runtime
returns `INSTALL_REQUIRED` (exit 8) with a pinned, workspace-relative Python
install proposal in `install_command`. The host interpreter is named by label
(`CPython-3.13`), not by filesystem path. The operator runs that command manually
after review. Automated installation from `doctor` is deferred to a later
release. See [Runtime bootstrap](runtime.md).

There is no `aef update` command. `upgrade` migrates workspace files only.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success, including RECORD or INGEST `CHANGE` and `NO_CHANGE`, and DOCTOR `PASS` |
| 1 | AUDIT found problems |
| 2 | Invalid command-line arguments |
| 3 | Invalid input document or unsupported option, including an invalid `--recording` or `--intake` file |
| 4 | Operation blocked without mutation, including a RECORD conflict, a missing ingest citation, or an unreadable existing record |
| 5 | Business `FAILED`, including a declared UPGRADE `MigrationFailure` |
| 6 | Filesystem or permission failure |
| 8 | DOCTOR `INSTALL_REQUIRED` — no compatible runtime; nothing was written |
| 70 | Unexpected internal failure |

Run `aef --help` and `aef COMMAND --help` for the authoritative installed
syntax.
