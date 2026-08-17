# Claude Code project integration

AEF can install a versioned guidance bridge in one initialized project:

```console
aef integrate claude
```

The only V1 scope is `project`, which is also the default. User and hybrid
scopes are not supported.

## What is loaded

The managed segment in `.claude/CLAUDE.md` imports these project-local files:

- `.agent/core/constitution.md`
- `.agent/core/autonomy.md`
- `.agent/core/learning.md`
- `.agent/core/levels.md`
- `.agent/core/scoring.md`

All five must be ordinary readable UTF-8 files. The bridge is verified
byte-for-byte. Content outside the managed segment remains user-owned.

`.agent/` and `.claude/CLAUDE.md` are sibling project-root entries. The bridge
uses relative imports such as `@../.agent/core/constitution.md`:

```text
project/
|-- .agent/
|   `-- core/
`-- .claude/
    `-- CLAUDE.md
```

Moving either `.agent/` or `.claude/CLAUDE.md` breaks those imports. Use the
AEF install, status, update, and remove commands instead of moving or rewriting
the managed segment manually. User content outside the segment is preserved.

## Status and updates

```console
aef integrate claude --status
aef integrate claude --dry-run
aef integrate claude
```

Status is always read-only. It reports bridge health separately from AEF audit
health. Malformed unmanaged Claude settings are warnings and are never repaired.
An incomplete EVALUATE transaction blocks installation, update, and removal,
but does not block status.

## Remove the managed segment

```console
aef integrate claude --remove --dry-run
aef integrate claude --remove
```

AEF never deletes `.claude/CLAUDE.md`; it remains empty if the AEF segment was
its only content. AEF does not alter `.claude/settings.json`,
`.claude/settings.local.json`, hooks, the project-root `CLAUDE.md`, parent
directories, or user-level Claude files.

## Security boundary

The integration is guidance-only. It neither grants tool authority nor proves
that every model action followed the doctrine. Claude must not approve, reject,
or recover EVALUATE merely because the bridge exists; those actions require an
explicit user request and the relevant AEF command.

## Claude Code memory boundaries

Claude Code's `CLAUDE.md` instructions and Auto Memory are separate mechanisms.
Auto Memory does not write to `.claude/CLAUDE.md`; Claude Code stores it under
`~/.claude/projects/<project>/memory/`. See the official
[Claude Code memory documentation](https://code.claude.com/docs/en/memory).

Claude Code can load user or organization instructions in addition to project
instructions. The guidance-only AEF bridge therefore does not guarantee that
no other Claude context exists. AEF does not inspect, modify, or normalize `~/.claude`,
and it does not scan for or disable Auto Memory. Use `/memory` and `/context`
to inspect the files Claude Code actually loaded.
