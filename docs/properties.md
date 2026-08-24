# Properties

This registry states invariants that must remain true regardless of
implementation. Each entry names the statement and the banc script, pytest
module, or review surface that covers it.

The adversarial banc lives in [`tests/adversarial/`](../tests/adversarial/README.md).
It is not a substitute for pytest: pytest checks expected paths; the banc
checks that the guarantee still holds when the input was not expected.

## Runtime and diagnostics

| # | Statement | Coverage |
|---|---|---|
| 1 | No binary shipped in the repository is executed. The process performs a single `execve` of AEF itself. | `tests/adversarial/10-epic3-runtime.sh` |
| 2 | Runtime commands make no network access (zero `socket`, zero `connect`). | `tests/adversarial/10-epic3-runtime.sh` |
| 3 | `.agent/` is unchanged unless the invoked command explicitly mutates it. `doctor` is read-only. | `tests/test_cli_doctor.py`; docs: [Runtime](runtime.md) |
| 4 | Process exit codes are exactly those in [Commands](commands.md). There is no extra mapping. | `tests/test_cli_protocol.py`; docs: [Commands](commands.md) |
| 5 | A CLI envelope is always returned, including when a workspace path is a FIFO, a Unix socket, or a directory where a file was expected. | `tests/test_runtime_confined_reads.py`; `tests/test_cli_output_modes.py` |
| 6 | Reads under the workspace go through registered sites and refuse any target outside the workspace, including through a chain of links. | `tests/test_runtime_confined_reads.py` |
| 7 | Nothing in the workspace is allowed to consume disproportionate memory or time. Archives are not opened. | `tests/adversarial/10-epic3-runtime.sh`; `tests/test_runtime_confined_reads.py` (`test_dependency_wheel_archive_not_opened`) |
| 8 | `doctor` asserts only what it observes: no proof of installation, no verification of archive contents — and the documentation says so. | `tests/test_cli_doctor.py`; docs: [Runtime](runtime.md) |
| 9 | Documented limit: the TOCTOU race on intermediate path components is not closed. An attacker who can rewrite the workspace during `doctor` can also edit `_version.py` directly. | Review only — [Runtime](runtime.md) |
| 33 | The `doctor` result payload does not contain the operator's home directory. `install_command` is workspace-relative and names the interpreter by label (`CPython-3.13`), not by filesystem path. The CLI envelope `workspace` field remains an absolute identity, as for every other command. | `tests/test_runtime_discovery.py` (`test_diagnose_fields_and_no_home_path`) |

## Governed writes

| # | Statement | Coverage |
|---|---|---|
| 10 | A lost write is never reported as success, and an applied write is always reported. This holds for every mutating command. | `tests/adversarial/01-concurrence-ingest.py`; `tests/adversarial/02-concurrence-declare.py`; `tests/adversarial/03-concurrence-record.py`; `tests/test_ingest_lot3.py` |
| 11 | Under contention the CLI blocks explicitly (`workspace_contention`). It never silently queues. Two guards raise `WorkspaceContentionError`: the load-time read of a governed file that vanishes (`filesystem.py` around the `load_workspace` loop) and the apply-time comparison of the caller's snapshot with a fresh `load_workspace` under `workspace_mutation_lock`. | `tests/adversarial/03-concurrence-record.py`; `tests/adversarial/14-ecrivain-externe.py`; `tests/test_lot3ter.py` (`test_load_workspace_missing_governed_file_raises_contention`); `tests/test_filesystem_safety.py` (`test_stale_snapshot_between_load_and_apply_raises_contention`) |
| 12 | A legitimate command started concurrently does not return a filesystem error. | `tests/adversarial/06-taux-erreur-fs.py` |
| 13 | The `--dry-run` verdict equals the apply verdict for every workspace state exercised. | `tests/adversarial/05-dryrun-vs-apply.py`; `tests/test_ingest_lot3.py` |
| 14 | Provenance union is deterministic and bounded; replay reaches a fixed point (`NO_CHANGE` from the second run). | `tests/test_ingest_lot3.py`; `tests/test_ingest_apply.py` |
| 15 | At the evidence cap the CLI blocks explicitly (`evidence_cap_exceeded`). It never returns `NO_CHANGE` that claims `events_accepted`. | `tests/adversarial/04-plafond-evidences.py` |
| 16 | Persisted identifiers are constrained in character set and length, and refused at write time if they are not. | `tests/test_ingest_lot3.py`; `tests/test_competency_declaration.py` |
| 17 | The interface states exactly what is derived, neither more nor less. | `tests/test_ingest_lot3.py` (`test_ingest_derived_prefixes_must_be_announced`) |
| 18 | The execution lock is a runtime artifact: it is not in `git status` after `init`, and not in history. | `tests/adversarial/11-hygiene-git.py` |

## Transactions and declaration

| # | Statement | Coverage |
|---|---|---|
| 19 | An ordinary interruption never leaves the workspace unrecoverable. Recovery reasons path by path. `inconsistent` is reserved for a path that matches neither before nor after, and names that path. | `tests/adversarial/07-crash-sigkill.py`; `tests/test_competency_declaration_recover.py` |
| 20 | A mutual guard is installed in both directions and on every path, including recovery. | `tests/adversarial/09-collision-identifiants.py`; `tests/test_lot5.py` |
| 21 | Identifier collision composes NFC and casefold, never one without the other. | `tests/adversarial/09-collision-identifiants.py`; `tests/test_competency_declaration.py` (`test_case_and_unicode_collision_matrix`) |
| 22 | The announced diff is the real diff, in dry-run and in apply. | `tests/test_competency_declaration_recover.py`; `tests/test_filesystem_safety.py` |
| 23 | Audit is scoped: an inherited workspace stays `PASS`, a legitimate promotion stays `PASS`, a hand-made fabrication turns red. | `tests/adversarial/08-audit-scopage.py`; `tests/test_ingest_audit.py` |

## Guidance integration

| # | Statement | Coverage |
|---|---|---|
| 24 | A marker inside a Markdown fence is not a marker. | `tests/adversarial/12-fence-marqueurs.py`; `tests/test_markdown_code.py`; `tests/test_lot5.py` |
| 25 | Preservation is byte-for-byte. Manual prose is never deleted automatically. | `tests/adversarial/12-fence-marqueurs.py`; `tests/test_guidance_integration.py` |
| 26 | A blocked door blocks the aggregate, and nothing is written. | `tests/adversarial/13-guidance-integrite.py`; `tests/test_lot5.py` |
| 27 | The file mode is preserved. A read-only file is not replaced. | `tests/adversarial/13-guidance-integrite.py`; `tests/test_lot5.py` |
| 28 | A single read feeds both the desired content and the guard. | `tests/test_lot5.py` (`test_cli_uses_one_snapshot_when_file_changes_after_first_read`) |
| 29 | Drift is detected by `--status`, not by `AUDIT` — and the documentation says so. | Review only for the documentation claim ([Claude integration](claude-integration.md), [Troubleshooting](troubleshooting.md)); `--status` behaviour: `tests/test_lot5.py`, `tests/test_cli_guidance.py` |

## Distribution and repository hygiene

| # | Statement | Coverage |
|---|---|---|
| 30 | A path ignored by `.gitignore` is not tracked by git. | `scripts/check_tracked_gitignored.py`; `tests/test_tracked_gitignored.py`; CI job `tests` |
| 31 | During a banc measurement, the imported `aef` package is the tree under test. | `tests/adversarial/00-setup.py`; `bancenv.verifier_arbre_importe` |
| 32 | The installable wheel contains the runtime package only. Tests and the banc ship in the sdist, not in the wheel. First-level trees `tests/`, `scripts/`, `fixtures/`, `docs/`, `.github/` and `src/` are refused in the wheel. | `scripts/verify_artifacts.py`; `tests/test_distribution_artifacts.py` (`test_release_artifact_inspector_rejects_wheel_containing_scripts`, `test_release_artifact_inspector_rejects_wheel_containing_fixtures`, `test_release_artifact_inspector_rejects_wheel_containing_non_runtime_tree`); `check-wheel-contents` in CI; review of `MANIFEST.in` (no prune of `tests/`) |
| 34 | The test suite shipped in the sdist runs to completion on an unpacked sdist, outside any git repository. Collection produces 0 error; the run produces 0 failed. The packaged suite includes `.github/workflows/*.yml` so it collects the same tests as the repository. Tests that need a git work tree still skip when `.git` is absent. | `scripts/verify_artifacts.py` (sdist ships `scripts/`, `fixtures/`, and `.github/*.yml`; the wheel ships none of them); `tests/test_distribution_artifacts.py` (`test_release_artifact_inspector_rejects_sdist_missing_scripts`, `test_release_artifact_inspector_rejects_sdist_missing_fixtures`, `test_release_artifact_inspector_rejects_sdist_missing_github`, `test_release_artifact_inspector_rejects_wheel_containing_scripts`, `test_release_artifact_inspector_rejects_wheel_containing_fixtures`); CI job `artifacts` step that unpacks the sdist and runs `pytest -q`; `tests/test_release_whitespace.py` and `tests/test_tracked_gitignored.py` skip when git is absent |
| 35 | `EVALUATE` never promotes without an explicit human decision document. Without `--decisions`, the CLI refuses with `interactive_input_required` and leaves career and evaluation state unchanged. | `tests/test_evaluate_v1.py` (`test_non_tty_evaluate_without_explicit_action_never_prompts`, `test_evaluate_without_decisions_does_not_promote`) |
