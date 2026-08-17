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

## Report a problem

Use a repository issue for reproducible non-security defects. Send suspected
vulnerabilities privately to hello@webdigit.be. Never attach secrets or private
workspace contents.
