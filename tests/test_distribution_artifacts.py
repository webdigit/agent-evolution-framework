import io
import tarfile
import zipfile

import pytest

from scripts.verify_artifacts import SCHEMAS, inspect_artifact


def _wheel(path, *, schemas=SCHEMAS, extra=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("aef/__init__.py", "")
        archive.writestr("aef/schemas/__init__.py", "")
        for name in schemas:
            archive.writestr(f"aef/schemas/{name}", "{}")
        for name in extra:
            archive.writestr(name, "local")


def _sdist(path):
    with tarfile.open(path, "w:gz") as archive:
        for name, content in {
            "agent-evolution-framework-0.1.0/README.md": b"README",
            "agent-evolution-framework-0.1.0/pyproject.toml": b"[project]",
            "agent-evolution-framework-0.1.0/docs/examples/connectors.json": b"{}",
            "agent-evolution-framework-0.1.0/docs/examples/reviews.json": b"{}",
            "agent-evolution-framework-0.1.0/docs/examples/evaluation-decisions.json": b"{}",
            "agent-evolution-framework-0.1.0/docs/examples/recording.json": b"{}",
            **{
                f"agent-evolution-framework-0.1.0/src/aef/schemas/{schema}": b"{}"
                for schema in SCHEMAS
            },
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_release_artifact_inspector_accepts_complete_wheel_and_sdist(tmp_path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    _wheel(wheel)
    _sdist(sdist)
    assert inspect_artifact(wheel)["schemas"] == sorted(SCHEMAS)
    assert inspect_artifact(sdist)["schemas"] == sorted(SCHEMAS)


def test_release_artifact_inspector_rejects_missing_schema(tmp_path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, schemas=SCHEMAS - {"knowledge.schema.json"})
    with pytest.raises(ValueError, match="schema set is incomplete"):
        inspect_artifact(wheel)


def test_release_artifact_inspector_rejects_unexpected_schema(tmp_path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, schemas=SCHEMAS | {"unexpected.schema.json"})
    with pytest.raises(ValueError, match="schema set is incomplete"):
        inspect_artifact(wheel)


def test_record_runtime_schemas_are_required_in_artifact_contract():
    assert {"record-submission.schema.json", "record.schema.json"} <= SCHEMAS


@pytest.mark.parametrize("path", [".agent/state.json", ".venv/marker", "aef/__pycache__/x.pyc"])
def test_release_artifact_inspector_rejects_local_state(tmp_path, path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=[path])
    with pytest.raises(ValueError, match="local or generated state"):
        inspect_artifact(wheel)
