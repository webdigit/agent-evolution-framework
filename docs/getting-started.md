# Getting started

## Install AEF

You can install the tagged Git source when Git is available. The release wheel
does not require Git:

```console
python -m pip install "https://github.com/webdigit/agent-evolution-framework/releases/download/v1.1.0/agent_evolution_framework-1.1.0-py3-none-any.whl"
```

Pip can still need network access for dependencies, so this is not a complete
air-gap installation. Download and verify `SHA256SUMS.txt` when installation
integrity must be checked.

## 1. Initialize one project

Change into the project that should own the AEF state:

```console
cd my-project
aef init --role generalist-agent
```

AEF creates `.agent/` in that project. It does not read AEF state from parent
directories, another project, or your home directory.

To inspect a new initialization without writing it, generate stable values
once in PowerShell:

```powershell
$instanceId = [guid]::NewGuid().ToString()
$createdAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

aef --workspace "C:\path\to\project" init `
  --role generalist-agent `
  --instance-id $instanceId `
  --created-at $createdAt `
  --dry-run

aef --workspace "C:\path\to\project" init `
  --role generalist-agent `
  --instance-id $instanceId `
  --created-at $createdAt
```

The second command must reuse the same values; generating new values would no
longer apply the plan that was reviewed.

## 2. Check the workspace

```console
aef audit
```

The audit is read-only. Fix reported missing or invalid state before running a
modifying operation.

## 3. Record a declared fact

RECORD stores an explicit declaration under `.agent/records/<record_id>.json`.
AEF computes the digest. The command does not update scores, career,
competency, knowledge, or evaluation state.

```console
aef record --recording docs/examples/recording.json --dry-run
aef record --recording docs/examples/recording.json
```

`--dry-run` writes nothing: it creates neither the records directory nor the
record file. Replaying the same valid document returns `NO_CHANGE`. Reusing
the same `record_id` with different content is blocked without rewriting the
existing file.

To derive signals from that journal, cite the persisted record in a separate
intake and run `aef ingest --intake FILE`. RECORD itself does not learn.

## 4. Use AEF with an agent

A natural-language request can be simple:

> Follow this project's AEF doctrine. Record outcomes and evidence, and notify
> me when a promotion recommendation needs review. Do not approve it for me.

AEF data remains ordinary JSON and Markdown under `.agent/`, so it can be
inspected and backed up with the project.

## 5. Optional Claude Code guidance

```console
aef integrate claude
aef integrate claude --status
```

This adds a managed segment to `.claude/CLAUDE.md` in the project. It is
guidance-only, project-scoped, and installs no hooks.

## 6. Machine-readable output

Human-readable output is automatic in a terminal. Scripts and agents should
request JSON explicitly:

```console
aef --json audit
aef --compact evaluate --list
```

Use `--dry-run` before a supported modification when you want to inspect the
planned bytes without writing them.

See [Canonical input files](input-files.md) for executable RECORD, INGEST,
DISCOVER, CONSOLIDATE, and EVALUATE documents.
