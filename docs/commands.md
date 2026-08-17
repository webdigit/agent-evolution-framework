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

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success, including `NO_CHANGE` |
| 1 | AUDIT found problems |
| 2 | Invalid command-line arguments |
| 3 | Invalid input document or unsupported option |
| 4 | Operation blocked without mutation |
| 5 | Reserved for a business operation returning `FAILED` |
| 6 | Filesystem or permission failure |
| 70 | Unexpected internal failure |

Run `aef --help` and `aef COMMAND --help` for the authoritative installed
syntax.
