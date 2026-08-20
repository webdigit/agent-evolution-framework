# Troubleshooting

## `aef` is not found

Try `python -m aef --version`. On Windows, also try `py -3.11 -m aef`. Ensure
the Python Scripts directory for the installation is on `PATH`.

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
unknown-version segment is preserved and blocks writes. Unmanaged Claude
settings warnings do not get repaired by AEF.

## Remove Claude guidance

```console
aef integrate claude --remove --dry-run
aef integrate claude --remove
```

The file is retained, even when empty. User content outside the segment remains
unchanged.

## A draft Release job failed

Read [Release delivery](release.md). Do not move an existing tag, delete a
divergent asset, or publish from a local checkout. Rerun the workflow for the
same tag only when the intended assets are unchanged.

## Report a problem

Use a repository issue for reproducible non-security defects. Send suspected
vulnerabilities privately to hello@webdigit.be. Never attach secrets or private
workspace contents.
