# Runtime bootstrap

AEF is a Python package. It becomes executable only through a compatible
Python interpreter: the `aef` console script or `python -m aef`. There is one
procedure for every operator and every agent.

## Diagnose first

```console
aef --version
python -m aef --version
aef doctor
aef --json doctor
```

`doctor` is **read-only**. It inspects the runtime. It does not write under
`.agent/`, does not modify an existing virtual environment, does not run `pip`,
and does not create environments. AEF never executes third-party binaries found
on `PATH` (including a binary named `aef`). Discovery prefers a declared
project environment (`.aef-venv`, `.venv`, or `venv`) when it carries a
readable `aef/_version.py`; otherwise it falls back to the running Python
module.

**What `found_package_version` means.** For `discovery_method: declared_env`,
the version is read from the project venv tree (`site-packages/aef/_version.py`)
without executing that interpreter. The envelope names the exact source path in
`declared_version_source`, reports `running_module_version` from the current CLI
process, and records `declared_env_tree_read:unverified` in `observations`.
**`doctor` does not verify that `pip install` succeeded** — there is no install
evidence index. A hand-crafted tree and a real install can produce the same human
disclaimer: `Trust: tree read only (pip install not verified)`.
For `discovery_method: python_module`, the version is the package imported by the
current CLI process.

**Confined reads and threat model.** Runtime reads under the workspace go through
`runtime_confined_io` (registered sites, bounded reads, regular files only — FIFOs
and directories are not opened for content). This protects against **static**
hostile content in a cloned repository (symlinks, named pipes, oversized files).
It does **not** protect against a concurrent process rewriting paths inside the
workspace during `doctor` (TOCTOU on intermediate directories). That race requires
an attacker who can already modify the workspace while you run `doctor`; such an
attacker can edit `_version.py` directly. An AST syntax guard in tests catches
obvious direct `Path.read_*` regressions but is not a complete coverage proof —
**per-site behavior tests** in `tests/test_runtime_confined_reads.py` (outbound
symlinks must not leak values read outside the workspace) are the enforcement
layer for confined reads.

**Dependency wheels and offline mode.** `offline_basis: self_attested_checksum`
requires a co-located `jsonschema-*.whl` file whose name matches the expected
pattern. **`doctor` does not open or parse wheel archives** — presence and size are
checked via `lstat` only (no zip decompression). Up to
`MAX_DEPENDENCY_WHEELS_TO_SCAN` (20) candidate wheels are inspected per directory.
This avoids zip-bomb amplification while honestly stating that filename presence
is not proof of a valid installable wheel.

`workspace_compatible` is `true` when `.agent/manifest.json` is present and
readable inside the workspace, `false` when initialization is absent, and `null`
when the manifest cannot be classified safely (for example a link escapes the
workspace).

Automated installation from `doctor` is planned for a later release.
Until then, when installation is required, `doctor` returns a **copyable command**
for the operator to run manually after review.

## When the runtime is missing

If `aef` and `python -m aef` cannot run, or `doctor` returns
`INSTALL_REQUIRED`, stop. Do not invent workspace state. Do not create, edit,
or delete files under `.agent/state/`.

Review the `install_command` in the `doctor` result, then run it manually in
a shell from any working directory — paths are workspace-absolute so the
target `.aef-venv` is always created beside the project:

```console
aef --json doctor
# copy install_command from the JSON result and run it
```

## Isolated install (manual)

The default target is a project-local environment named `.aef-venv`. When that
name is already occupied, the proposed `install_command` targets the first free
name in order: `.aef-venv`, then `.aef-venv-<platform>`. An existing `.venv`
or `.aef-venv` is never rewritten by the proposal.

A local wheel whose digest matches a co-located sidecar
(`.whl.sha256` or `SHA256SUMS.txt`) is classified as `checksum_matched`. That
attests **internal consistency of a pair supplied by the same source** — not an
independent trust anchor. Without a matching digest the wheel is
`available_unverified` (never plain `available`).

`network_required: false` appears only when `offline_basis` is
`self_attested_checksum`: checksum matched **and** a non-empty `jsonschema-*.whl`
file (not `jsonschema-specifications-*`) is present beside the AEF wheel.
**Archive contents are not verified** — only filename and file size. This does
**not** guarantee a complete air-gap install: transitive dependencies
(`attrs`, `referencing`, `rpds-py`, and others) are not verified. Treat offline
mode as a convenience hint, not a promise. Otherwise the proposal pins against
PyPI:

```console
python -m venv /path/to/project/.aef-venv
/path/to/project/.aef-venv/bin/python -m pip install --isolated --no-cache-dir --index-url https://pypi.org/simple "agent-evolution-framework==<pinned-version>"
```

On Windows, `py -3.11 -m venv` and `.aef-venv\Scripts\python.exe` are the
equivalent entry points. The host system interpreter is not the install target.

## After install

Always verify the new environment:

```console
python -m aef --version
```

If the project already contains `.agent/manifest.json`, also run:

```console
python -m aef audit
```

A missing manifest is not an installation failure. `aef upgrade` migrates
workspace files; it does not install the package. There is no `aef update`
command.

## Observations

When the runtime is otherwise healthy (`decision: OK`) but multiple local
wheels are present, `doctor` records `ambiguous_local_wheels` in
`observations` without blocking. Ambiguity blocks installation only when the
runtime is not yet valid.

## Machine result

`aef --json doctor` uses the `aef.cli/v1` envelope with `command=DOCTOR`.
`INSTALL_REQUIRED` is a first-class status (exit code 8). It is not `BLOCKED`,
`FAIL`, or `ERROR`.
