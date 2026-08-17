from pathlib import Path
import os

import pytest

from conftest import installed_aef_script


def test_installed_script_can_live_in_windows_scripts_directory(tmp_path: Path):
    executable = tmp_path / "hostedtoolcache" / "Python" / "x64" / "python.exe"
    script = executable.parent / "Scripts" / "aef.exe"
    script.parent.mkdir(parents=True)
    script.touch()

    resolved = installed_aef_script(
        executable=executable,
        scripts_directory=script.parent,
        path_lookup=lambda _name: None,
        script_name="aef.exe",
    )

    assert resolved == script


def test_installed_script_can_live_in_posix_bin_directory(tmp_path: Path):
    script = tmp_path / "bin" / "aef"
    script.parent.mkdir()
    script.touch()

    resolved = installed_aef_script(
        executable=script.parent / "python",
        scripts_directory=script.parent,
        path_lookup=lambda _name: None,
        script_name="aef",
    )

    assert resolved == script


def test_installed_script_falls_back_beside_interpreter(tmp_path: Path):
    executable = tmp_path / "legacy" / "python"
    script = executable.with_name("aef")
    script.parent.mkdir()
    script.touch()

    resolved = installed_aef_script(
        executable=executable,
        scripts_directory=tmp_path / "empty",
        path_lookup=lambda _name: None,
        script_name="aef",
    )

    assert resolved == script


@pytest.mark.parametrize("name", ["aef.exe", "aef"])
def test_installed_script_falls_back_to_exact_name_on_path(
    tmp_path: Path, name: str
):
    script = tmp_path / "path" / name
    script.parent.mkdir()
    script.touch()
    requested = []
    launchers = {name: script}

    resolved = installed_aef_script(
        executable=tmp_path / "python",
        scripts_directory=tmp_path / "empty",
        path_lookup=lambda candidate: requested.append(candidate) or launchers.get(candidate),
        script_name=name,
    )

    assert resolved == script
    assert requested == [name]


@pytest.mark.parametrize(
    ("requested_name", "available_name"),
    [("aef.exe", "aef"), ("aef", "aef.exe")],
)
def test_path_fallback_rejects_a_differently_named_launcher(
    tmp_path: Path, requested_name: str, available_name: str
):
    available = tmp_path / "path" / available_name
    available.parent.mkdir()
    available.touch()
    launchers = {available_name: available}

    with pytest.raises(AssertionError):
        installed_aef_script(
            executable=tmp_path / "python",
            scripts_directory=tmp_path / "empty",
            path_lookup=lambda candidate: launchers.get(candidate),
            script_name=requested_name,
        )


def test_native_launcher_name_is_used_without_override(tmp_path: Path):
    native_name = "aef.exe" if os.name == "nt" else "aef"
    script = tmp_path / "path" / native_name
    script.parent.mkdir()
    script.touch()
    requested = []
    launchers = {native_name: script}

    resolved = installed_aef_script(
        executable=tmp_path / "runtime" / "python",
        scripts_directory=tmp_path / "empty",
        path_lookup=lambda candidate: requested.append(candidate) or launchers.get(candidate),
    )

    assert resolved == script
    assert requested == [native_name]


def test_missing_installed_script_is_reported(tmp_path: Path):
    with pytest.raises(
        AssertionError,
        match="The installed aef console script could not be located",
    ):
        installed_aef_script(
            executable=tmp_path / "python",
            scripts_directory=tmp_path / "empty",
            path_lookup=lambda _name: None,
            script_name="aef",
        )
