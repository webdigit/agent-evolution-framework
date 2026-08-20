# Changelog

All notable changes to AEF are documented in this file.

## [Unreleased]

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
