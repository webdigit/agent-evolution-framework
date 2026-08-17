# Installation

AEF V1 supports Python 3.11 through 3.14. Installing the command makes AEF
available on your machine; running `aef init` activates it only in the selected
project.

## Windows

Install Python from python.org, then open PowerShell:

```powershell
py -3.11 -m pip install "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.0.0"
aef --version
```

If `aef` is not on `PATH`, use `py -3.11 -m aef` or add Python's Scripts
directory to `PATH`.

## macOS and Linux

```console
python3 -m pip install --user "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.0.0"
python3 -m aef --version
```

Use a virtual environment when you want an isolated installation:

```console
python3 -m venv .aef-venv
. .aef-venv/bin/activate
python -m pip install "agent-evolution-framework @ git+https://github.com/webdigit/agent-evolution-framework.git@v1.0.0"
```

On Windows, activate with `.aef-venv\Scripts\Activate.ps1`.

## Verify both entry points

```console
aef --help
python -m aef --help
```

AEF is source-available under the PolyForm Internal Use License 1.0.0. It is
not open-source software. Read the repository `LICENSE` before use.
