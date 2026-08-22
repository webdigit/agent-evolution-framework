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
and does not create environments. AEF does not execute third-party binaries
found on `PATH` (including a binary named `aef`); discovery uses the running
Python module or a tree read of a declared project environment.

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
name already holds an environment from another platform, the proposal uses
`.aef-venv-<platform>` instead. An existing `.venv` or `.aef-venv` is never
rewritten.

A local wheel whose digest matches a co-located sidecar
(`.whl.sha256` or `SHA256SUMS.txt`) is classified as `checksum_matched`. That
attests **internal consistency of a pair supplied by the same source** — not an
independent trust anchor. Without a matching digest the wheel is
`available_unverified` (never plain `available`).

`network_required: false` appears only when `offline_basis` is
`self_attested_checksum`: checksum matched **and** dependency wheels such as
`jsonschema*.whl` are present beside the AEF wheel. Otherwise the proposal pins
against PyPI:

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
