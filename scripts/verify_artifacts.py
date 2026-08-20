"""Verify that AEF release archives contain runtime resources and no local state."""

from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


SCHEMAS = {
    "capability.schema.json",
    "career.schema.json",
    "competencies.schema.json",
    "evaluation.schema.json",
    "exploration.schema.json",
    "knowledge.schema.json",
    "manifest.schema.json",
    "migrations.schema.json",
    "policies.schema.json",
    "record-submission.schema.json",
    "record.schema.json",
    "supervision.schema.json",
}
DOCUMENTATION_EXAMPLES = {
    "connectors.json", "reviews.json", "evaluation-decisions.json",
    "recording.json",
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
SECRET_OR_LOCAL_NAMES = {
    ".env",
    "credentials.json",
    "secrets.json",
}


def _members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return sorted(name for name in archive.namelist() if not name.endswith("/"))
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return sorted(member.name for member in archive if member.isfile())
    raise ValueError(f"unsupported artifact: {path.name}")


def _is_private_path(member: PurePosixPath) -> bool:
    if PRIVATE_PARTS.intersection(member.parts):
        return True
    parts = member.parts
    for index in range(len(parts) - 1):
        if (parts[index], parts[index + 1]) in PRIVATE_PREFIXES:
            return True
    return member.name in SECRET_OR_LOCAL_NAMES


def inspect_artifact(path: Path) -> dict[str, object]:
    members = _members(path)
    normalized = [PurePosixPath(member) for member in members]
    if any(FORBIDDEN_PARTS.intersection(member.parts) for member in normalized):
        raise ValueError(f"local or generated state found in {path.name}")
    if any(_is_private_path(member) for member in normalized):
        raise ValueError(f"private path found in {path.name}")
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
    return {"artifact": path.name, "files": members, "schemas": sorted(schema_members)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(argv)
    report = [inspect_artifact(path) for path in args.artifacts]
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
