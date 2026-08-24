# Installation

AEF V1 supports Python 3.11 through 3.14. Installing the command makes AEF
available on your machine; running `aef init` activates it only in the selected
project. Diagnose an existing checkout with `aef doctor` or
`aef --json doctor` before creating a new environment. See
[Runtime bootstrap](runtime.md).

Going through Python is intentional. AEF is a project runtime, not an editor
plugin: the CLI must be able to refuse even when the agent forgets the skill.
See [Concepts](concepts.md#a-runtime-not-a-harness-plugin).

## Install from Git

Installing from the tagged repository requires Git.

### Windows

Install Python from python.org, then open PowerShell:

```powershell
py -3.11 -m pip install "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.2.0"
aef --version
```

If `aef` is not on `PATH`, use `py -3.11 -m aef` or add Python's Scripts
directory to `PATH`.

### macOS and Linux

```console
python3 -m pip install --user "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.2.0"
python3 -m aef --version
```

Use a virtual environment when you want an isolated installation:

```console
python3 -m venv .aef-venv
. .aef-venv/bin/activate
python -m pip install "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.2.0"
```

On Windows, activate with `.aef-venv\Scripts\Activate.ps1`.

## Install the release wheel without Git

The release wheel does not require Git. On Windows:

```powershell
py -m pip install "https://github.com/webdigit/agent-evolution-framework/releases/download/v1.2.0/agent_evolution_framework-1.2.0-py3-none-any.whl"
```

With a `python` launcher:

```console
python -m pip install "https://github.com/webdigit/agent-evolution-framework/releases/download/v1.2.0/agent_evolution_framework-1.2.0-py3-none-any.whl"
```

Pip may still require network access to obtain runtime dependencies. The wheel
alone is therefore not a complete air-gap installation. Download
`SHA256SUMS.txt` from the [v1.2.0 release](https://github.com/webdigit/agent-evolution-framework/releases/tag/v1.2.0)
when you need to verify the wheel before installation.

## Verify both entry points

```console
aef --help
python -m aef --help
```

## Diagnose the runtime after install

```console
aef doctor
aef --json doctor
```

`INSTALL_REQUIRED` means no compatible runtime is available. `doctor` is
read-only: it proposes a pinned Python install command in `install_command` but
does **not** execute it. Copy the command from the JSON or human output and run
it manually after review. Automated installation from `doctor` will
return in a later release.

AEF never installs into the system interpreter and never rewrites an existing
project virtual environment. A local wheel with a matching co-located digest is
`checksum_matched` (self-attested consistency only). Offline-complete proposals
(`network_required: false`) require `offline_basis: self_attested_checksum` —
checksum matched **and** a non-empty `jsonschema-*.whl` beside the AEF wheel.
Offline mode does not verify transitive dependencies; treat it as a hint, not a
complete air-gap guarantee. Unverified local wheels are not proposed with
`--no-index`. Pip proposals pin `--index-url https://pypi.org/simple` with
`--isolated --no-cache-dir`. There is no `aef update` command.

AEF is source-available under the PolyForm Internal Use License 1.0.0. It is
not open-source software. Read the repository `LICENSE` before use.
