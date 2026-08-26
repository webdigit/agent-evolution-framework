"""Write validated capture envelopes under the resolved deposit directory."""

from __future__ import annotations

import json
from pathlib import Path

from .deposit_intake import validate_deposit_submission
from .strict_json import validate_strict_json
from .workspace_resolution import WorkspaceResolution, resolve_deposit_dir


class InvalidDepositFilenameError(ValueError):
    """Raised when a deposit filename is unsafe or unusable."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_deposit_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise InvalidDepositFilenameError(
            "invalid_deposit_filename",
            "deposit filename must be non-empty.",
        )
    if filename != Path(filename).name or filename in {".", ".."}:
        raise InvalidDepositFilenameError(
            "invalid_deposit_filename",
            "deposit filename must be a single path segment.",
        )
    if not filename.endswith(".json"):
        raise InvalidDepositFilenameError(
            "invalid_deposit_filename",
            "deposit envelope must be a .json file.",
        )
    return filename


def write_deposit_envelope(
    resolution: WorkspaceResolution,
    filename: str,
    document: dict[str, object],
) -> Path:
    """Validate and write one envelope under ``<workspace>/.aef-deposit/``."""
    validated = validate_deposit_submission(document)
    validate_deposit_filename(filename)
    deposit_dir = resolve_deposit_dir(resolution)
    deposit_dir.mkdir(parents=True, exist_ok=True)
    target = deposit_dir / filename
    validate_strict_json(validated)
    payload = json.dumps(
        validated,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    target.write_text(payload + "\n", encoding="utf-8")
    return target
