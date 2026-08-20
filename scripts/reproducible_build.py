"""Build AEF wheel and sdist twice from clean output directories."""

from __future__ import annotations

import argparse
import gzip
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prepare_draft_release import (
    DraftReleaseError,
    locate_release_artifacts,
    sha256_file,
)
from scripts.verify_artifacts import assert_regular_tar_member


def source_date_epoch(commit: str, *, cwd: Path) -> int:
    result = subprocess.run(
        ["git", "log", "-1", "--pretty=%ct", commit],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        raise DraftReleaseError(f"cannot derive SOURCE_DATE_EPOCH from {commit}")
    return int(result.stdout.strip())


def release_build_command(outdir: Path) -> list[str]:
    return [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(outdir)]


def assert_no_isolated_dependency_install(stdout: str, stderr: str) -> None:
    combined = f"{stdout}\n{stderr}".casefold()
    markers = (
        "creating isolated environment",
        "installing packages in isolated environment",
        "installing build dependencies",
    )
    if any(marker in combined for marker in markers):
        raise DraftReleaseError("build installed a floating backend dependency")


def build_once(outdir: Path, *, epoch: int, cwd: Path) -> None:
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    result = subprocess.run(
        release_build_command(outdir),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "build failed"
        raise DraftReleaseError(detail)
    assert_no_isolated_dependency_install(result.stdout, result.stderr)
    wheel, sdist = locate_release_artifacts(outdir)
    normalize_sdist_timestamps(sdist, epoch)


def _canonical_tarinfo(member: tarfile.TarInfo, *, epoch: int) -> tarfile.TarInfo:
    rebuilt = tarfile.TarInfo(name=member.name)
    rebuilt.size = member.size
    rebuilt.mtime = epoch
    rebuilt.mode = member.mode
    rebuilt.type = member.type
    rebuilt.uid = 0
    rebuilt.gid = 0
    rebuilt.uname = ""
    rebuilt.gname = ""
    rebuilt.linkname = member.linkname
    return rebuilt


def normalize_sdist_timestamps(path: Path, epoch: int) -> None:
    with tarfile.open(path, "r:gz") as source:
        members = []
        contents: dict[str, bytes] = {}
        for member in source.getmembers():
            try:
                assert_regular_tar_member(member)
            except ValueError as exc:
                raise DraftReleaseError(str(exc)) from exc
            rebuilt = _canonical_tarinfo(member, epoch=epoch)
            if member.isfile():
                if member.name in contents:
                    raise DraftReleaseError("duplicate archive members")
                extracted = source.extractfile(member)
                if extracted is None:
                    raise DraftReleaseError(f"sdist member is unreadable: {member.name}")
                payload = extracted.read()
                rebuilt.size = len(payload)
                contents[member.name] = payload
            members.append(rebuilt)
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w") as dest:
        for member in members:
            if member.isfile():
                dest.addfile(member, io.BytesIO(contents[member.name]))
            else:
                dest.addfile(member)
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=epoch) as handle:
        handle.write(tar_bytes.getvalue())
    path.write_bytes(compressed.getvalue())


def assert_identical_release_builds(first: Path, second: Path) -> None:
    left_wheel, left_sdist = locate_release_artifacts(first)
    right_wheel, right_sdist = locate_release_artifacts(second)
    if left_wheel.name != right_wheel.name or left_sdist.name != right_sdist.name:
        raise DraftReleaseError("rebuilt artifact names differ")
    if sha256_file(left_wheel) != sha256_file(right_wheel):
        raise DraftReleaseError("wheel is not reproducible")
    if sha256_file(left_sdist) != sha256_file(right_sdist):
        raise DraftReleaseError("sdist is not reproducible")


def build_reproducible_artifacts(*, commit: str, outdir: Path, cwd: Path) -> tuple[Path, Path]:
    epoch = source_date_epoch(commit, cwd=cwd)
    first = outdir.parent / f"{outdir.name}.repro-a"
    second = outdir.parent / f"{outdir.name}.repro-b"
    try:
        build_once(first, epoch=epoch, cwd=cwd)
        build_once(second, epoch=epoch, cwd=cwd)
        assert_identical_release_builds(first, second)
        if outdir.exists():
            shutil.rmtree(outdir)
        outdir.mkdir(parents=True)
        wheel, sdist = locate_release_artifacts(first)
        copied_wheel = Path(shutil.copy2(wheel, outdir / wheel.name))
        copied_sdist = Path(shutil.copy2(sdist, outdir / sdist.name))
    finally:
        shutil.rmtree(first, ignore_errors=True)
        shutil.rmtree(second, ignore_errors=True)
    return copied_wheel, copied_sdist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--cwd", default=Path.cwd(), type=Path)
    args = parser.parse_args(argv)
    try:
        build_reproducible_artifacts(
            commit=args.commit, outdir=args.outdir, cwd=args.cwd
        )
    except DraftReleaseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
