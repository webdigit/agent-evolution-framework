from pathlib import Path
import os
import sys
import sysconfig


def aef_cli_argv(*args: str | os.PathLike[str]) -> list[str]:
    """Build a CLI argv that invokes ``python -m aef``.

    Tests must never spawn the console script (``aef`` / ``aef.exe``): on some
    hosts WDAC blocks it while ``sys.executable -m aef`` remains usable.
    """
    return [sys.executable, "-m", "aef", *(str(item) for item in args)]


def installed_aef_script(
    *,
    executable: str | os.PathLike[str] = sys.executable,
    scripts_directory: str | os.PathLike[str] | None = None,
    path_lookup=None,
    script_name: str | None = None,
) -> Path:
    """Locate the console script installed for the active Python interpreter.

    Reserved for unit tests of locator behaviour (``test_cli_launcher``). Do not
    use the returned path as a subprocess executable — use ``aef_cli_argv``.
    """
    import shutil

    if path_lookup is None:
        path_lookup = shutil.which
    name = script_name or ("aef.exe" if os.name == "nt" else "aef")
    scripts = Path(scripts_directory or sysconfig.get_path("scripts"))
    candidates = (scripts / name, Path(executable).with_name(name))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # PATH is a last resort for test environments whose installed script is not
    # represented by sysconfig; it does not establish interpreter ownership.
    discovered = path_lookup(name)
    if discovered and Path(discovered).is_file():
        return Path(discovered)
    raise AssertionError("The installed aef console script could not be located.")
