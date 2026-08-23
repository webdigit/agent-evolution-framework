# AEF — Agent Evolution Framework

AEF gives a project a durable, reviewable memory for how an AI agent works. It
stores project-local doctrine, competencies, evidence, learning, connector
discovery, and human-approved progression under `.agent/` instead of leaving
those decisions inside a transient chat.

AEF is useful when you want an agent to improve within explicit boundaries:
work can produce evidence and promotion recommendations, but authority and
level changes remain controlled by policy and explicit human decisions.

## Install

AEF V1 requires Python 3.11 or later. Install the tagged source from GitHub
when Git is available:

```console
python -m pip install "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.1.0"
```

Alternatively, install the release wheel directly. This method does not require Git:

```console
python -m pip install "https://github.com/webdigit/agent-evolution-framework/releases/download/v1.1.0/agent_evolution_framework-1.1.0-py3-none-any.whl"
```

Pip may still need network access to install dependencies, so the wheel alone
is not a complete air-gap installation. For a verified installation, download
`SHA256SUMS.txt` from the same release and verify the wheel before installing.

Verify both entry points:

```console
aef --version
python -m aef --version
```

See [Installation](docs/installation.md) for platform-specific setup and
isolated-environment options.

## Start a project

Run AEF from your project directory. The role describes the agent's primary
project responsibility; it does not grant operating-system or tool authority.

```console
cd my-project
aef init --role generalist-agent
aef audit
```

Initialization creates a project-local `.agent/` workspace. AEF does not
inherit state from a parent project or a user directory.

For a reproducible first dry-run, generate the identity and timestamp once and
reuse the same values for the real initialization:

```powershell
$instanceId = [guid]::NewGuid().ToString()
$createdAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

aef init --role generalist-agent --instance-id $instanceId --created-at $createdAt --dry-run
aef init --role generalist-agent --instance-id $instanceId --created-at $createdAt
```

In plain language, you can now tell your agent: “Use this project's AEF rules,
record evidence from completed work, and tell me when a promotion review is
available.” AEF keeps the resulting state inspectable and replay-safe.

## Activate AEF guidance for agents

```console
aef integrate all
aef integrate claude --status
```

The integration installs a managed segment in project-root `AGENTS.md` (doctrine
citations) plus doorbells `CLAUDE.md` / `GEMINI.md`. It does not write
user-level Claude settings, install hooks, or create a new fat
`.claude/CLAUDE.md` bridge. An existing brownfield bridge is left alone.

Guidance is **guidance-only**. It does not technically enforce tool use, grant
authority, or allow an agent to approve, reject, or recover an evaluation
without an explicit user request. See
[Claude and agent guidance](docs/claude-integration.md).

## Essential commands

```console
aef init --role generalist-agent
aef audit
aef record --recording recording.json --dry-run
aef record --recording recording.json
aef discover --snapshot connectors.json
aef consolidate --reviews reviews.json
aef evaluate --list
aef evaluate --decisions decisions.json
aef integrate all --status
```

Use `--dry-run` on supported modifying commands to inspect the planned result.
Terminal output is human-readable by default; automation should always request
the stable machine protocol explicitly:

```console
aef --json audit
aef --compact evaluate --list
```

The complete syntax, output modes, and exit codes are documented in
[Commands](docs/commands.md).

## Documentation

- [Installation](docs/installation.md)
- [Getting started](docs/getting-started.md)
- [Claude project integration](docs/claude-integration.md)
- [Command reference](docs/commands.md)
- [Properties](docs/properties.md)
- [Canonical input files](docs/input-files.md)
- [Core concepts](docs/concepts.md)
- [Evaluation recovery](docs/recovery.md)
- [Release delivery](docs/release.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Release history](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## V1 boundaries

- State and activation are project-local by default.
- INIT, AUDIT, RECORD, DISCOVER, CONSOLIDATE, EVALUATE, and Claude project
  integration are available; UPGRADE is not part of V1.
- RECORD persists an explicit declared-fact file under `.agent/records/`. AEF
  computes the digest. Replay of a valid matching file returns `NO_CHANGE`;
  the same `record_id` with different content is blocked. RECORD does not
  create scores or update career, competency, knowledge, or evaluation state.
- DISCOVER records connector capabilities but grants no authority.
- CONSOLIDATE reviews existing rule lifecycles; it does not autonomously invent
  knowledge or principles.
- Promotions require an explicit human EVALUATE decision and never skip a
  level.
- Filesystem writes are confined and atomic per file. EVALUATE adds a recovery
  journal for its multi-file transaction.
- The Claude integration is guidance-only and project-scoped; no V1 hooks are
  installed.

## License

AEF is **source-available, not open source**. It is licensed under the
[PolyForm Internal Use License 1.0.0](LICENSE). Internal professional use and
internal modification are permitted by that license; redistribution,
sublicensing, hosted access for third parties, and installation for third
parties require separate written permission from WEBDIGIT SRL. See
[Commercial licensing](COMMERCIAL-LICENSE.md) for contact information.

Copyright © 2026 WEBDIGIT SRL.
