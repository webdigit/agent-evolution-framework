# Claude Code and agent guidance integration

AEF installs **project-local guidance doors**. Doctrinal rules live once in
`AGENTS.md`. `CLAUDE.md` and `GEMINI.md` at the project root are doorbells that
point at that commun. Guidance is not permission, not a hook, and not a host
settings change.

```console
aef integrate agents
aef integrate claude
aef integrate gemini
aef integrate all
```

`aef integrate claude` remains the public Claude entry point. It installs the
root `CLAUDE.md` doorbell and co-installs `AGENTS.md` when needed. It does
**not** create a new fat bridge under `.claude/`.

The only V1 scope is `project`, which is also the default. User and hybrid
scopes are not supported.

## What is loaded

### Commun — `AGENTS.md`

The managed segment cites (does not copy) the five doctrine files:

- `.agent/core/constitution.md`
- `.agent/core/autonomy.md`
- `.agent/core/learning.md`
- `.agent/core/levels.md`
- `.agent/core/scoring.md`

It also points once at `docs/runtime.md` for the Python runtime path
(`aef doctor` before transitions). It does not triplicate `INSTALL_REQUIRED`.

### Doorbells

- Root `CLAUDE.md` — managed segment contains `@AGENTS.md` only.
- `GEMINI.md` — one plain-language pointer to `AGENTS.md`.

### Brownfield — `.claude/CLAUDE.md`

Workspaces that already have an `AEF:CLAUDE-PROJECT` bridge keep it. That
legacy segment imported doctrine with relative paths such as
`@../.agent/core/constitution.md` because `.agent/` and `.claude/CLAUDE.md`
are sibling project-root entries:

```text
project/
|-- .agent/
|   `-- core/
`-- .claude/
    `-- CLAUDE.md
```

Status reports that legacy bridge. Install of the new doors does not rewrite
it. `--remove` on `integrate claude` removes the root doorbell first; a later
`--remove` can clear a legacy segment when the root doorbell is already gone.
AEF never deletes `.claude/CLAUDE.md`; it may remain empty if the managed
segment was its only content.

## Status and updates

```console
aef integrate claude --status
aef integrate all --status
aef integrate claude --dry-run
```

Status is read-only. It reports guidance health separately from AEF audit
health. Malformed unmanaged Claude settings are warnings and are never
repaired. Health of guidance doors is reported here, not by `aef audit`
(which stays centered on `.agent/`). A hand-edited managed block is `BLOCKED`
on `--status`; `aef audit` still returns `PASS` with no findings for that
drift — that is the V1 contract (A5), not a detection gap. An incomplete
EVALUATE transaction blocks installation and removal, but does not block
status.

Replay of an identical installed segment returns `NO_CHANGE`.

## Enforcement

`enforcement: guidance_only`. AEF does not claim to control every agent action.
Do not present levels, XP, Trust, or recommendations as permission. Claude must
not approve, reject, or recover EVALUATE merely because a doorbell exists;
those actions require an explicit user request and the relevant AEF command.

## Claude Code memory boundaries

Claude Code's `CLAUDE.md` instructions and Auto Memory are separate mechanisms.
Auto Memory does not write to `.claude/CLAUDE.md` or the managed AEF bridge
segment. By default, Claude Code stores Auto Memory under
`~/.claude/projects/<project>/memory/`. Claude Code can configure a different
location through `autoMemoryDirectory` in its supported user or policy
settings. AEF does not read those settings to resolve `autoMemoryDirectory`.
See the official
[Claude Code memory documentation](https://code.claude.com/docs/en/memory).

Claude Code can load user or organization instructions in addition to project
instructions. The guidance-only AEF bridge therefore does not guarantee that
no other Claude context exists. AEF does not inspect, modify, or normalize `~/.claude`,
and it does not scan for or disable Auto Memory. Use `/memory` and `/context`
to inspect the files Claude Code actually loaded.
