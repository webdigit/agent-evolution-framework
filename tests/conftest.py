from pathlib import Path
import os
import shutil
import sys
import sysconfig


def installed_aef_script(
    *,
    executable: str | os.PathLike[str] = sys.executable,
    scripts_directory: str | os.PathLike[str] | None = None,
    path_lookup=shutil.which,
    script_name: str | None = None,
) -> Path:
    """Locate the console script installed for the active Python interpreter."""
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
