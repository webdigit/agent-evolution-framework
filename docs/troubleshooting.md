# Troubleshooting

## `aef` is not found

Try `python -m aef --version`. On Windows, also try `py -3.11 -m aef`. Ensure
the Python Scripts directory for the installation is on `PATH`.

If neither entry point runs, treat the situation as `INSTALL_REQUIRED`. Do not
edit `.agent/state/`. Run `aef doctor` or `aef --json doctor` when a compatible
interpreter can import the package, review the pinned proposal in
`install_command`, and run it manually. See [Runtime bootstrap](runtime.md).

## `INSTALL_REQUIRED`

`aef doctor` reports this status when no compatible AEF runtime is available.
It is not an audit failure and not a blocked workspace mutation. An existing
`.venv` from another platform is left untouched. A local wheel is used only
when its hash matches.

## `doctor` returns `BLOCKED`

`aef doctor` reports `BLOCKED` (exit 4) when diagnosis cannot proceed safely.
Read `blocked_cause` and `blocked_path` in the JSON envelope, or the `Cause` /
`Path` lines in human output. Common causes:

- `invalid_expected_package_version` — `.agent/runtime-requirements.json` is
  missing, malformed, or carries a non-PEP-440 version.
- `external_env` — a declared virtual environment symlink escapes the
  workspace (`blocked_path` names the declared env entry).
- `ambiguous_local_wheels` — multiple local wheels match and none can be chosen
  while installation is still required.

Fix the reported path or requirement, then rerun `aef doctor`. `BLOCKED` is not
`INSTALL_REQUIRED` and proposes no install command.

## INIT is blocked

Pass an explicit role:

```console
aef init --role generalist-agent
```

An existing incompatible identity or version is not overwritten. Preserve the
workspace and inspect the JSON result with `aef --json init ...`.

## `dry_run_requires_stable_inputs`

A first INIT dry-run cannot generate an identity or timestamp that would differ
from the subsequent write. Supply both options and reuse the same values for
the dry-run and application:

```powershell
$instanceId = [guid]::NewGuid().ToString()
$createdAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

aef init --role generalist-agent --instance-id $instanceId --created-at $createdAt --dry-run
aef init --role generalist-agent --instance-id $instanceId --created-at $createdAt
```

Neither command should regenerate `--instance-id` or `--created-at` between
planning and application.

## Install without Git

Install the release wheel when Git is unavailable. This does not require Git,
but pip can still need network access for dependencies and it is not a complete
air-gap workflow. Download `SHA256SUMS.txt` with the wheel when verification is
required. See [Installation](installation.md).

## AUDIT reports missing or invalid state

AUDIT never repairs files. Restore the affected project-local file from a known
backup or version control. Do not copy state from another project.

## A modifying command requires recovery

Follow [EVALUATE recovery](recovery.md). The reserved transaction path fails
closed even when it is an empty file, directory, symlink, or unreadable entry.

## Claude integration is blocked

Run:

```console
aef integrate claude --status
```

Do not hand-edit the managed markers. A modified, duplicate, incomplete, or
unknown-version segment is preserved and blocks writes. Detect that drift with
`aef integrate <door> --status` (status `BLOCKED`). `aef audit` does not
inspect those segments and will stay `PASS`. Unmanaged Claude settings
warnings do not get repaired by AEF.

A marker quoted inside a markdown fence (`` ``` `` or `~~~`) or a four-space
indented code block is documentation, not an installation. `--remove` must not
strip those quoted bytes.

## Remove Claude guidance

```console
aef integrate claude --remove --dry-run
aef integrate claude --remove
```

The file is retained, even when empty. User content outside the segment remains
unchanged.

## INGEST is blocked

`aef ingest` is blocked when a cited `record_id` is missing, unreadable, or
the intake `digest` does not match the persisted record. Nothing is written
under `.agent/knowledge/`. This is not `INSTALL_REQUIRED` and not an upgrade
recovery. Persist the record first, copy its digest into the intake, then
retry. An invalid intake document is exit 3, not a blocked citation.

## A draft Release job failed

Read [Release delivery](release.md). Do not move an existing tag, delete a
divergent asset, or publish from a local checkout. If the job reports
`draft Release disappeared after upload`, the draft may already exist:
GitHub's tag lookup returns 404 for drafts. Inspect that draft. Rerun the
workflow for the same tag only when the intended assets are unchanged and
the prepare script can resolve drafts by list and release ID.

## Report a problem

Use a repository issue for reproducible non-security defects. Send suspected
vulnerabilities privately to hello@webdigit.be. Never attach secrets or private
workspace contents.
