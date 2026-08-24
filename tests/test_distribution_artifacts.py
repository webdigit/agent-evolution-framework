import io
import tarfile
import zipfile

import pytest

from scripts.verify_artifacts import SCHEMAS, inspect_artifact


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo()
    info.filename = name
    return info


def _wheel(path, *, schemas=SCHEMAS, extra=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("aef/__init__.py", "")
        archive.writestr("aef/schemas/__init__.py", "")
        for name in schemas:
            archive.writestr(f"aef/schemas/{name}", "{}")
        for name in extra:
            archive.writestr(_zip_info(name), "local")


def _sdist(
    path,
    extra=(),
    extra_named=None,
    duplicates=(),
    extra_infos=(),
    include_suite_resources=True,
):
    files = {
        "agent-evolution-framework-0.1.0/README.md": b"README",
        "agent-evolution-framework-0.1.0/pyproject.toml": b"[project]",
        "agent-evolution-framework-0.1.0/docs/examples/connectors.json": b"{}",
        "agent-evolution-framework-0.1.0/docs/examples/reviews.json": b"{}",
        "agent-evolution-framework-0.1.0/docs/examples/evaluation-decisions.json": b"{}",
        "agent-evolution-framework-0.1.0/docs/examples/recording.json": b"{}",
        "agent-evolution-framework-0.1.0/docs/examples/ingest.json": b"{}",
        "agent-evolution-framework-0.1.0/docs/examples/competency-declaration.json": b"{}",
        **{
            f"agent-evolution-framework-0.1.0/src/aef/schemas/{schema}": b"{}"
            for schema in SCHEMAS
        },
    }
    if include_suite_resources:
        files["agent-evolution-framework-0.1.0/scripts/verify_artifacts.py"] = b""
        files["agent-evolution-framework-0.1.0/fixtures/minimal/manifest.json"] = b"{}"
    for name in extra:
        files[f"agent-evolution-framework-0.1.0/{name}"] = b"private"
    files.update(extra_named or {})
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        for name in duplicates:
            content = b"duplicate"
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        for info in extra_infos:
            archive.addfile(info)


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
    assert {
        "record-submission.schema.json",
        "record.schema.json",
        "ingest-submission.schema.json",
        "competency-declaration-submission.schema.json",
    } <= SCHEMAS


def test_upgrade_transaction_schema_is_required_in_artifact_contract():
    assert "upgrade-transaction.schema.json" in SCHEMAS


@pytest.mark.parametrize("path", [".agent/state.json", ".venv/marker", "aef/__pycache__/x.pyc"])
def test_release_artifact_inspector_rejects_local_state(tmp_path, path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=[path])
    with pytest.raises(ValueError, match="local or generated state"):
        inspect_artifact(wheel)


PRIVATE_PATHS = [
    "docs/prompts/README.md",
    "Docs/Prompts/secret.md",
    "_bmad/core/workflow.md",
    "_bmad-output/status.yaml",
    ".agents/skills/x.md",
    ".env",
    ".env.production",
    "credentials.json",
    "secrets.json",
    "private-key.pem",
    "aef-cockpit.local.json",
    "id_rsa",
]


UNSAFE_PATHS = [
    r"docs\prompts\secret.md",
    "../secret.md",
    "foo/./bar.md",
    "foo//bar.md",
    "/abs/secret.md",
]


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_release_artifact_inspector_rejects_private_path_in_wheel(tmp_path, path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=[path])
    with pytest.raises(ValueError, match="private path"):
        inspect_artifact(wheel)


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_release_artifact_inspector_rejects_private_path_in_sdist(tmp_path, path):
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    _sdist(sdist, extra=[path])
    with pytest.raises(ValueError, match="private path"):
        inspect_artifact(sdist)


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_release_artifact_inspector_rejects_unsafe_path_in_wheel(tmp_path, path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=[path])
    with pytest.raises(ValueError, match="unsafe archive member"):
        inspect_artifact(wheel)


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_release_artifact_inspector_rejects_unsafe_path_in_sdist(tmp_path, path):
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    _sdist(sdist, extra_named={path: b"private"})
    with pytest.raises(ValueError, match="unsafe archive member"):
        inspect_artifact(sdist)


def test_release_artifact_inspector_rejects_case_collision_in_wheel(tmp_path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=["aef/extra.py", "aef/Extra.py"])
    with pytest.raises(ValueError, match="case-colliding"):
        inspect_artifact(wheel)


def test_release_artifact_inspector_rejects_case_collision_in_sdist(tmp_path):
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    _sdist(
        sdist,
        extra_named={
            "agent-evolution-framework-0.1.0/README.MD": b"dup",
        },
    )
    with pytest.raises(ValueError, match="case-colliding"):
        inspect_artifact(sdist)


def test_release_artifact_inspector_rejects_duplicate_member_in_wheel(tmp_path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=["aef/extra.py", "aef/extra.py"])
    with pytest.raises(ValueError, match="duplicate archive members"):
        inspect_artifact(wheel)


def test_release_artifact_inspector_rejects_duplicate_member_in_sdist(tmp_path):
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    _sdist(sdist, duplicates=["agent-evolution-framework-0.1.0/README.md"])
    with pytest.raises(ValueError, match="duplicate archive members"):
        inspect_artifact(sdist)


@pytest.mark.parametrize(
    "tar_type,linkname",
    [
        (tarfile.SYMTYPE, "README.md"),
        (tarfile.LNKTYPE, "agent-evolution-framework-0.1.0/README.md"),
        (tarfile.CHRTYPE, ""),
        (tarfile.BLKTYPE, ""),
        (tarfile.FIFOTYPE, ""),
    ],
)
def test_release_artifact_inspector_rejects_non_regular_sdist_member(
    tmp_path, tar_type, linkname
):
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    info = tarfile.TarInfo("agent-evolution-framework-0.1.0/weird")
    info.type = tar_type
    info.linkname = linkname
    if tar_type in {tarfile.CHRTYPE, tarfile.BLKTYPE}:
        info.devmajor = 1
        info.devminor = 3
    _sdist(sdist, extra_infos=[info])
    with pytest.raises(ValueError, match="non-regular archive member"):
        inspect_artifact(sdist)


def test_release_artifact_inspector_rejects_sdist_missing_scripts(tmp_path):
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    _sdist(sdist, include_suite_resources=False)
    with pytest.raises(ValueError, match="missing scripts"):
        inspect_artifact(sdist)


def test_release_artifact_inspector_rejects_sdist_missing_fixtures(tmp_path):
    sdist = tmp_path / "aef-0.1.0.tar.gz"
    _sdist(
        sdist,
        include_suite_resources=False,
        extra_named={
            "agent-evolution-framework-0.1.0/scripts/verify_artifacts.py": b"#",
        },
    )
    with pytest.raises(ValueError, match="missing fixtures"):
        inspect_artifact(sdist)


def test_release_artifact_inspector_rejects_wheel_containing_scripts(tmp_path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=["scripts/verify_artifacts.py"])
    with pytest.raises(ValueError, match="wheel contains scripts"):
        inspect_artifact(wheel)


def test_release_artifact_inspector_rejects_wheel_containing_fixtures(tmp_path):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=["fixtures/minimal/manifest.json"])
    with pytest.raises(ValueError, match="wheel contains fixtures"):
        inspect_artifact(wheel)


@pytest.mark.parametrize(
    "member, fragment",
    [
        ("tests/test_x.py", "tests"),
        ("tests/adversarial/14-ecrivain-externe.py", "tests"),
        ("docs/x.md", "docs"),
        (".github/workflows/ci.yml", ".github"),
        ("src/aef/__init__.py", "src"),
    ],
)
def test_release_artifact_inspector_rejects_wheel_containing_non_runtime_tree(
    tmp_path, member, fragment
):
    wheel = tmp_path / "aef-0.1.0-py3-none-any.whl"
    _wheel(wheel, extra=[member])
    with pytest.raises(ValueError, match=f"wheel contains {fragment}"):
        inspect_artifact(wheel)
