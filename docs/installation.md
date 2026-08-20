# Installation

AEF V1 supports Python 3.11 through 3.14. Installing the command makes AEF
available on your machine; running `aef init` activates it only in the selected
project.

## Install from Git

Installing from the tagged repository requires Git.

### Windows

Install Python from python.org, then open PowerShell:

```powershell
py -3.11 -m pip install "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.1.0"
aef --version
```

If `aef` is not on `PATH`, use `py -3.11 -m aef` or add Python's Scripts
directory to `PATH`.

### macOS and Linux

```console
python3 -m pip install --user "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.1.0"
python3 -m aef --version
```

Use a virtual environment when you want an isolated installation:

```console
python3 -m venv .aef-venv
. .aef-venv/bin/activate
python -m pip install "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.1.0"
```

On Windows, activate with `.aef-venv\Scripts\Activate.ps1`.

## Install the release wheel without Git

The release wheel does not require Git. On Windows:

```powershell
py -m pip install "https://github.com/webdigit/agent-evolution-framework/releases/download/v1.1.0/agent_evolution_framework-1.1.0-py3-none-any.whl"
```

With a `python` launcher:

```console
python -m pip install "https://github.com/webdigit/agent-evolution-framework/releases/download/v1.1.0/agent_evolution_framework-1.1.0-py3-none-any.whl"
```

Pip may still require network access to obtain runtime dependencies. The wheel
alone is therefore not a complete air-gap installation. Download
`SHA256SUMS.txt` from the [v1.1.0 release](https://github.com/webdigit/agent-evolution-framework/releases/tag/v1.1.0)
when you need to verify the wheel before installation.

## Verify both entry points

```console
aef --help
python -m aef --help
```

AEF is source-available under the PolyForm Internal Use License 1.0.0. It is
not open-source software. Read the repository `LICENSE` before use.
