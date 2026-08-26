# Changelog

All notable changes to AEF are documented in this file.

## [Unreleased]

## [2.1.0] - 2026-08-26

Package 2.1.0 still implements the V1 workspace contract
(`framework_version` / `schema_version` `1.0.0`, profile `aef-v1`). There is
no breaking change to submitted input schemas or workspace files. On a valid V1
workspace already at `schema_version` `1.0.0`, `aef upgrade --check` and
`aef upgrade` return `NO_CHANGE`.

### Added

- **`aef integrate runtime`:** installs or refreshes the managed environment map
  at `docs/runtime.md` as a pure snapshot of `aef doctor`. `doctor` remains
  read-only; when the map no longer matches the host it is reported as
  **périmé** (stale), not as catalog tampering.
- **Workspace resolution:** when `--workspace` is omitted, the CLI walks up from
  the current directory to the nearest ancestor that contains `.agent/`, and
  reports when relocation occurred. An explicit `--workspace` path is never
  relocated.
- **Submitted identifier rules:** the `:` separator is reserved for identifiers
  derived by AEF. A colon in a submitted `record_id`, event `id`, `pattern_key`,
  or `competency` is rejected with an explicit message (`use '.' or '-'
  instead`), not a silent schema mismatch.

### Changed

- CI test runs now pass `-rs` to pytest so every skipped test and its reason
  appear in the job log.

### Documentation

- Review conduct for implementation units is stated in `AGENTS.md` (blocking
  vs same-commit findings, two-pass ceiling).

### Boundaries

- No breaking change. Input submission schemas are unchanged; the workspace
  contract stays `1.0.0`.
- `aef upgrade` migrates workspace files toward the contract of the already
  installed package only; it is not a software update and returns `NO_CHANGE`
  on a conformant workspace.
- `integrate runtime` does not run automatically on install or upgrade; the
  operator runs it in the consumer workspace when needed.

## [2.0.0] - 2026-08-24

Package 2.0.0 still implements the V1 workspace contract
(`framework_version` / `schema_version` `1.0.0`, profile `aef-v1`). A
`.agent/` created by 1.2.0 needs no migration. `aef upgrade --check` on
that workspace returns `NO_CHANGE`. The major bump is for documented
CLI output that changed, not for a new workspace schema.

### Added

- INGEST: `aef ingest --intake FILE` cites persisted records and writes
  only `.agent/knowledge/knowledge.json`.
- Competency declaration: `aef competency declare --declaration FILE`
  births a competency at L1 with an explicit human decision.
- DOCTOR: `aef doctor` diagnoses the runtime without writing, without
  running `pip`, and without creating environments.
- Guidance doors for `agents`, `gemini`, and atomic `integrate all`,
  with fence-aware marker detection.
- Adversarial banc under `tests/adversarial/`, with a CI workflow
  (`adversarial.yml`) on Linux, nightly, on demand, or when a pull
  request carries the `adversarial` label.
- Property registry in `docs/properties.md`.
- Guard against tracked gitignored paths:
  `scripts/check_tracked_gitignored.py`.
- Guard against non-runtime trees in the wheel (`tests/`, `scripts/`,
  `fixtures/`, `docs/`, `.github/`, `src/`): `WHEEL_FORBIDDEN_TREES` in
  `scripts/verify_artifacts.py`.

### Changed / Compatibility

Observable output differences since 1.2.0:

- Ingest at the evidence cap: 1.2.0 returned `NO_CHANGE` (exit 0).
  2.0.0 returns `BLOCKED` (exit 4) with `meta.reason`
  `evidence_cap_exceeded`.
- Concurrent mutation: 2.0.0 returns `BLOCKED` with `meta.reason`
  `workspace_contention`. 1.2.0 returned a filesystem error or a false
  success.
- Record while an EVALUATE journal is open: `BLOCKED` (exit 4) with
  `meta.reason` `evaluation_recovery_required`.
- The filesystem error code emitted for the current guidance doors is
  `guidance_filesystem_error`. 1.2.0 emitted
  `claude_integration_filesystem_error` for the Claude integration
  path. The previous code remains only for the brownfield
  `.claude/CLAUDE.md` adapter.
- `doctor.install_command` is workspace-relative (no absolute host
  path). The host interpreter is named by label (`CPython-3.13`), not
  by filesystem path. `doctor` itself is new in 2.0.0; this is the
  frozen output shape.
- A marker inside a Markdown fence is not a marker: `--status` reports
  a different `installed` value for the same file than 1.2.0.
- Duplicate JSON keys in a governed input (including RECORD, which
  existed in 1.2.0) are rejected with `duplicate_json_key` (exit 3).
  1.2.0 kept the last value.
- Human BLOCKED text for an unsafe guidance write is now
  `[BLOCKED] Guidance integration cannot be updated safely`.

### Documentation

- "V1" names the workspace contract, not the Python package version.
  User-facing docs and CLI help state that explicitly.

### Boundaries

- Two version axes. The package is 2.0.0. The workspace contract stays
  `1.0.0`. `claude_integration.py` still refuses a manifest whose
  `framework_version` or `schema_version` is not `"1.0.0"`.
- There is no `aef update` command. `upgrade` migrates workspace files
  only. On a valid V1 workspace already at `schema_version` `1.0.0`,
  it returns `NO_CHANGE`.
- `doctor` never executes the proposed install command.
- The distribution remains source-available under the PolyForm Internal
  Use License 1.0.0 and is not open source.

## [1.2.0] - 2026-08-20

### Added

- UPGRADE V1.x migrates the project-local workspace toward the contract of
  the already installed package via `aef upgrade --check`, `--dry-run`,
  apply, `--recover --dry-run`, and `--recover`.
- A dedicated journal at `.agent/state/upgrade-transaction.json`
  (`aef.upgrade-transaction/v1`) protects that mutation. It is distinct
  from the EVALUATE journal.
- AUDIT reports `upgrade-recovery-required`, and distinct findings when a
  journal is bound to another workspace or to divergent schema versions.

### Documentation

- UPGRADE command forms and recovery: journal confinement under `.agent/`,
  workspace identity binding, and rollback of `prepared` plus an all-after
  disk state.

### Boundaries

- Upgrade is not a software update. There is no `aef update` command and
  no `--target-schema` flag. The productive workspace target remains
  `schema_version` `1.0.0`.
- Lab helpers `upgrade_project` and `apply_framework_release` stay unexposed.
- Recovery never follows a journal path outside `.agent/`. A mixed
  before/after set or unknown hash stays `BLOCKED` without writing.

## [1.1.2] - 2026-08-20

### Fixed

- Draft Release lookup no longer treats GitHub's tag-endpoint 404 as a
  disappeared Release. Drafts are found by listing Releases and refreshed
  by release ID after upload.

## [1.1.1] - 2026-08-20

### Added

- GitHub Actions prepares a draft Release from a `vX.Y.Z` tag. Publication
  remains a separate human decision. Retry rebuilds pin the build frontend
  and backend, timestamp artifacts from the tagged commit, and bind the
  draft to that SHA.

## [1.1.0] - 2026-08-20

### Added

- RECORD V1 persists an explicit declared-fact recording with
  `aef record --recording FILE [--dry-run]`.
- Replay of a valid matching record returns `NO_CHANGE` without rewriting the
  file.

### Changed

- Replay revalidates an existing persisted record, including its canonical
  digest, before returning `NO_CHANGE`.
- Clearer public `invalid_json` message for invalid JSON, including a
  `--recording` file. The message no longer claims every parse failure is a
  workspace document.

### Documentation

- RECORD command, exit-code coverage, and a minimal `aef.record.submit/v1`
  example.

### Boundaries

- RECORD writes only `.agent/records/<record_id>.json`. It does not create
  scores, XP, competencies, rules, or evaluations.

## [1.0.1] - 2026-08-17

### Fixed

- Clearer INIT dry-run stable-input guidance.

### Documentation

- Direct wheel installation without requiring Git.
- Claude bridge directory layout.
- Claude Code Auto Memory and user-memory boundaries.

## [1.0.0]

Initial public V1 release.

### Added

- Official project-local AEF V1 initialization profile and normative doctrine.
- Read-only workspace audit with persisted-state validation.
- Deterministic connector discovery from explicit strict-JSON snapshots.
- Human-approved knowledge rule lifecycle consolidation.
- Pending promotion recommendations and explicit human EVALUATE decisions.
- Recoverable multi-file EVALUATE transactions.
- Project-scoped, guidance-only Claude Code integration.
- Human, stable JSON, compact JSON, and dry-run CLI modes.
- Confined atomic filesystem adapters and packaged runtime JSON Schemas.

### Boundaries

- UPGRADE, Claude hooks, user-scoped Claude integration, and autonomous action
  execution are not included in V1.
- The distribution is source-available under the PolyForm Internal Use License
  1.0.0 and is not open source.

[1.0.0]: https://github.com/webdigit/agent-evolution-framework/releases/tag/v1.0.0
[1.0.1]: https://github.com/webdigit/agent-evolution-framework/compare/v1.0.0...v1.0.1
[1.1.0]: https://github.com/webdigit/agent-evolution-framework/compare/v1.0.1...v1.1.0
[1.1.1]: https://github.com/webdigit/agent-evolution-framework/compare/v1.1.0...v1.1.1
[1.1.2]: https://github.com/webdigit/agent-evolution-framework/compare/v1.1.1...v1.1.2
[1.2.0]: https://github.com/webdigit/agent-evolution-framework/compare/v1.1.2...v1.2.0
[2.0.0]: https://github.com/webdigit/agent-evolution-framework/compare/v1.2.0...v2.0.0
