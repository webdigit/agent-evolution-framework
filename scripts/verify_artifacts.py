"""Verify that AEF release archives contain runtime resources and no local state."""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath


SCHEMAS = {
    "capability.schema.json",
    "career.schema.json",
    "competencies.schema.json",
    "competency-declaration-submission.schema.json",
    "evaluation.schema.json",
    "exploration.schema.json",
    "knowledge.schema.json",
    "learning-validation-submission.schema.json",
    "manifest.schema.json",
    "migrations.schema.json",
    "policies.schema.json",
    "ingest-submission.schema.json",
    "record-submission.schema.json",
    "record.schema.json",
    "supervision.schema.json",
    "upgrade-transaction.schema.json",
}
DOCUMENTATION_EXAMPLES = {
    "connectors.json", "reviews.json", "evaluation-decisions.json",
    "recording.json",
    "ingest.json",
    "competency-declaration.json",
    "learning-validation.json",
    "learning-ingest-hypothesis.json",
    "learning-principle-validation.json",
}
FORBIDDEN_PARTS = {
    ".agent", ".venv", "__pycache__", ".pytest_cache", "build", "dist",
}
PRIVATE_PARTS = {
    ".agent",
    ".agents",
    "_bmad",
    "_bmad-output",
}
PRIVATE_PREFIXES = (
    ("docs", "prompts"),
)
PRIVATE_KEY_NAMES = {
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
LOCAL_CONFIG_SUFFIXES = (
    ".local.json",
    ".local.toml",
    ".local.yaml",
    ".local.yml",
)
SECRET_SUFFIXES = (
    ".key",
    ".p12",
    ".pem",
    ".pfx",
)
FORBIDDEN_PARTS_FOLD = {part.casefold() for part in FORBIDDEN_PARTS}
PRIVATE_PARTS_FOLD = {part.casefold() for part in PRIVATE_PARTS}
PRIVATE_PREFIXES_FOLD = tuple(
    tuple(part.casefold() for part in prefix) for prefix in PRIVATE_PREFIXES
)
ALLOWED_TAR_TYPES = {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}
# First-level trees that belong in the sdist (or nowhere), never in the wheel.
WHEEL_FORBIDDEN_TREES = ("tests", "scripts", "fixtures", "docs", ".github", "src")


def _zip_raw_names(path: Path) -> list[str]:
    data = path.read_bytes()
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("unsafe archive member")
    count = int.from_bytes(data[eocd + 10:eocd + 12], "little")
    offset = int.from_bytes(data[eocd + 16:eocd + 20], "little")
    names: list[str] = []
    position = offset
    for _ in range(count):
        if data[position:position + 4] != b"PK\x01\x02":
            raise ValueError("unsafe archive member")
        flags = int.from_bytes(data[position + 8:position + 10], "little")
        name_length = int.from_bytes(data[position + 28:position + 30], "little")
        extra_length = int.from_bytes(data[position + 30:position + 32], "little")
        comment_length = int.from_bytes(data[position + 32:position + 34], "little")
        raw = data[position + 46:position + 46 + name_length]
        encoding = "utf-8" if flags & 0x800 else "cp437"
        name = raw.decode(encoding)
        if not name.endswith("/"):
            names.append(name)
        position += 46 + name_length + extra_length + comment_length
    return sorted(names)


def assert_regular_tar_member(member: tarfile.TarInfo) -> None:
    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
        raise ValueError("non-regular archive member")
    if member.type not in ALLOWED_TAR_TYPES:
        raise ValueError("non-regular archive member")


def _sdist_file_names(path: Path) -> list[str]:
    names: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            assert_regular_tar_member(member)
            if member.isfile():
                names.append(member.name)
    return sorted(names)


def _members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        return _zip_raw_names(path)
    if path.name.endswith(".tar.gz"):
        return _sdist_file_names(path)
    raise ValueError(f"unsupported artifact: {path.name}")


def member_parts(name: str) -> tuple[str, ...]:
    if "\\" in name or "\x00" in name or name.startswith("/"):
        raise ValueError("unsafe archive member")
    parts = name.split("/")
    if not parts or any(part == "" or part in {".", ".."} for part in parts):
        raise ValueError("unsafe archive member")
    return tuple(parts)


def assert_safe_members(names: list[str]) -> list[tuple[str, ...]]:
    normalized = [member_parts(name) for name in names]
    seen: dict[tuple[str, ...], tuple[str, ...]] = {}
    exact: set[tuple[str, ...]] = set()
    for parts in normalized:
        if parts in exact:
            raise ValueError("duplicate archive members")
        exact.add(parts)
        key = tuple(part.casefold() for part in parts)
        previous = seen.get(key)
        if previous is not None and previous != parts:
            raise ValueError("case-colliding archive members")
        seen[key] = parts
    return normalized


def _is_secret_or_local_name(name: str) -> bool:
    folded = name.casefold()
    if folded.startswith(".env"):
        return True
    if folded.endswith(SECRET_SUFFIXES) or folded.endswith(LOCAL_CONFIG_SUFFIXES):
        return True
    if "credentials" in folded or "secrets" in folded or "private-key" in folded:
        return True
    return folded in PRIVATE_KEY_NAMES


def _wheel_contains_tree(normalized: list[PurePosixPath], names: list[str], tree: str) -> bool:
    prefix = f"{tree}/"
    return any(tree in member.parts for member in normalized) or any(
        name.startswith(prefix) or f"/{prefix}" in f"/{name}" for name in names
    )


def _is_private_folded(folded: tuple[str, ...]) -> bool:
    if PRIVATE_PARTS_FOLD.intersection(folded):
        return True
    for index in range(len(folded) - 1):
        if folded[index:index + 2] in PRIVATE_PREFIXES_FOLD:
            return True
    return _is_secret_or_local_name(folded[-1])


def inspect_artifact(path: Path) -> dict[str, object]:
    members = _members(path)
    parts_list = assert_safe_members(members)
    folded_list = [tuple(part.casefold() for part in parts) for parts in parts_list]
    if any(FORBIDDEN_PARTS_FOLD.intersection(folded) for folded in folded_list):
        raise ValueError(f"local or generated state found in {path.name}")
    if any(_is_private_folded(folded) for folded in folded_list):
        raise ValueError(f"private path found in {path.name}")
    normalized = [PurePosixPath(*parts) for parts in parts_list]
    schema_members = {
        member.name for member in normalized
        if tuple(member.parts[-3:-1]) == ("aef", "schemas")
        and member.suffix == ".json"
    }
    if schema_members != SCHEMAS:
        raise ValueError(f"runtime schema set is incomplete in {path.name}")
    if path.suffix == ".whl" and not any(
        member.as_posix() == "aef/schemas/__init__.py" for member in normalized
    ):
        raise ValueError("wheel is missing the schema resource package")
    if path.suffix == ".whl" and any(
        tuple(member.parts[-3:-1]) == ("docs", "examples")
        for member in normalized
    ):
        raise ValueError("wheel contains non-runtime documentation examples")
    if path.name.endswith(".tar.gz") and not all(
        any(member.name == required for member in normalized)
        for required in ("README.md", "pyproject.toml")
    ):
        raise ValueError("sdist is missing release metadata")
    if path.name.endswith(".tar.gz"):
        examples = {
            member.name for member in normalized
            if tuple(member.parts[-3:-1]) == ("docs", "examples")
        }
        if examples != DOCUMENTATION_EXAMPLES:
            raise ValueError("sdist documentation example set is incomplete")
        names = [member.as_posix() for member in normalized]
        if not any("/scripts/" in name and name.endswith(".py") for name in names):
            raise ValueError("sdist is missing scripts/")
        if not any("/fixtures/" in name for name in names):
            raise ValueError("sdist is missing fixtures/")
        if not any("/.github/" in name and name.endswith(".yml") for name in names):
            raise ValueError("sdist is missing .github/")
    if path.suffix == ".whl":
        names = [member.as_posix() for member in normalized]
        for tree in WHEEL_FORBIDDEN_TREES:
            if _wheel_contains_tree(normalized, names, tree):
                raise ValueError(f"wheel contains {tree}/")
    return {"artifact": path.name, "files": members, "schemas": sorted(schema_members)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        report = [inspect_artifact(path) for path in args.artifacts]
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
