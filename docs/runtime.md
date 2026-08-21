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

`doctor` inspects the runtime. It does not write under `.agent/`, and it does
not modify an existing virtual environment. Without `--install` or
`--reuse-env`, AEF does not execute third-party binaries found on `PATH`
(including a binary named `aef`); discovery falls through to the running
Python module or a tree read of a declared project environment.

## When the runtime is missing

If `aef` and `python -m aef` cannot run, or `doctor` returns
`INSTALL_REQUIRED`, stop. Do not invent workspace state. Do not create, edit,
or delete files under `.agent/state/`.

Propose an isolated, pinned install. Wait for explicit consent. Consent is the
`--install` flag after the proposed command has been reviewed:

```console
aef doctor --install
```

Without `--install`, AEF does not run `pip`, create a virtual environment,
download a package, or execute third-party binaries from `PATH`.

## Isolated install

The default target is a project-local environment named `.aef-venv`. When that
name already holds an environment from another platform, AEF uses
`.aef-venv-<platform>` instead. An existing `.venv` or `.aef-venv` is never
rewritten.

Without `--reuse-env`, `doctor --install` never executes a Python interpreter
that already existed before this invocation. If `.aef-venv` is occupied, AEF
creates a fresh environment under a free distinct name (typically
`.aef-venv-<platform>`) or refuses reuse. With `--reuse-env`, AEF may probe
an existing `.aef-venv` **or** `.aef-venv-<platform>` interpreter only.

A local wheel whose hash matches is classified as `verified`. Without a
matching digest it is `available_unverified` (never plain `available`). Offline
`network_required: false` additionally requires dependency wheels in the same
find-links directory (at least `jsonschema*.whl`):

```console
python -m venv .aef-venv
python -m pip install --isolated --no-cache-dir --no-index --find-links . agent_evolution_framework-*.whl
```

Otherwise the proposed command pins the package against the public index:

```console
python -m venv .aef-venv
python -m pip install --isolated --no-cache-dir --index-url https://pypi.org/simple "agent-evolution-framework==<pinned-version>"
```

Consent flags:

```console
aef doctor --install
aef doctor --install --reuse-env
```

On Windows, `py -3.11 -m venv` and `.aef-venv\Scripts\python.exe` are the
equivalent entry points. The host system interpreter is not the install
target.

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

## Machine result

`aef --json doctor` uses the `aef.cli/v1` envelope with `command=DOCTOR`.
`INSTALL_REQUIRED` is a first-class status (exit code 8). It is not `BLOCKED`,
`FAIL`, or `ERROR`.
