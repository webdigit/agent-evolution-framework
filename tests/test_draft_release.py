from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts.prepare_draft_release import (
    DesiredAsset,
    DraftReleaseError,
    ExistingAsset,
    ExistingRelease,
    apply_draft_release,
    assert_commit_is_on_main,
    assert_package_version_matches_tag,
    locate_release_artifacts,
    operator_summary,
    parse_attribution_body,
    parse_release_tag,
    parse_sha256sums,
    plan_draft_release,
    redact_secrets,
    render_release_body,
    render_sha256sums,
    version_from_wheel,
    write_sha256sums,
)
from scripts.reproducible_build import (
    assert_identical_release_builds,
    assert_no_isolated_dependency_install,
    build_once,
    release_build_command,
    source_date_epoch,
)


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_TREE_FILES = (
    ".github/workflows/release.yml",
    ".github/workflows/ci.yml",
    "scripts/prepare_draft_release.py",
    "scripts/reproducible_build.py",
    "docs/release.md",
    "README.md",
)
_missing = [rel for rel in _REQUIRED_TREE_FILES if not (ROOT / rel).is_file()]
if _missing:
    pytest.skip(
        "not in this tree: " + ", ".join(_missing),
        allow_module_level=True,
    )
WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
SCRIPT = (ROOT / "scripts/prepare_draft_release.py").read_text(encoding="utf-8")
BUILD_SCRIPT = (ROOT / "scripts/reproducible_build.py").read_text(encoding="utf-8")
DOCS = (ROOT / "docs/release.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
RELEASE_PINS = [
    "build==1.5.0",
    "check-wheel-contents==0.6.3",
    "setuptools==84.0.0",
    "twine==6.2.0",
]


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.name", "Release Test")
    _git(repository, "config", "user.email", "release@example.invalid")
    (repository / "marker.txt").write_text("main\n", encoding="utf-8")
    _git(repository, "add", "marker.txt")
    _git(repository, "commit", "-q", "-m", "main")
    return repository


def _asset(name: str, content: bytes) -> DesiredAsset:
    return DesiredAsset(name=name, content=content)


def _desired() -> tuple[DesiredAsset, ...]:
    return (
        _asset("SHA256SUMS.txt", b"sums\n"),
        _asset("aef-1.2.0.tar.gz", b"sdist"),
        _asset("aef-1.2.0-py3-none-any.whl", b"wheel"),
    )


class MemoryGitHub:
    def __init__(self) -> None:
        self.releases: dict[str, dict] = {}
        self.deleted: list[str] = []
        self.published: list[str] = []
        self.next_id = 1
        self.contents: dict[str, bytes] = {}

    def get_release(self, release_id: int):
        for record in self.releases.values():
            if record["id"] == release_id:
                return record
        return None

    def get_release_by_tag(self, tag: str):
        return self.releases.get(tag)

    def create_draft_release(self, *, tag: str, version: str, commit: str):
        record = {
            "id": self.next_id,
            "tag_name": tag,
            "name": f"AEF {version}",
            "draft": True,
            "html_url": f"https://example.test/{tag}",
            "upload_url": (
                f"https://uploads.example.test/{self.next_id}/assets{{?name,label}}"
            ),
            "body": render_release_body(tag=tag, commit=commit, version=version),
            "assets": [],
        }
        self.next_id += 1
        self.releases[tag] = record
        return record

    def download_asset(self, asset_url: str) -> bytes:
        return self.contents[asset_url]

    def upload_asset(self, upload_url: str, asset: DesiredAsset):
        for record in self.releases.values():
            if str(record["id"]) not in upload_url:
                continue
            url = f"https://example.test/assets/{asset.name}"
            self.contents[url] = asset.content
            record["assets"].append(
                {
                    "name": asset.name,
                    "size": asset.size,
                    "digest": f"sha256:{asset.sha256}",
                    "url": url,
                }
            )
            return record["assets"][-1]
        raise DraftReleaseError("upload target is missing")

    def delete_asset(self, name: str) -> None:
        self.deleted.append(name)


def test_valid_tag_matches_package_version():
    assert parse_release_tag("v1.2.0") == "1.2.0"
    assert_package_version_matches_tag("1.2.0", "v1.2.0")


@pytest.mark.parametrize("tag", ["v1.2", "1.2.0", "v1.2.0-rc1", "v1.2.0.1", "release-1.2.0"])
def test_malformed_tag_is_rejected(tag):
    with pytest.raises(DraftReleaseError, match="malformed release tag"):
        parse_release_tag(tag)


def test_package_version_different_from_tag_is_rejected():
    with pytest.raises(DraftReleaseError, match="does not match tag"):
        assert_package_version_matches_tag("1.2.0", "v1.3.0")


def test_tag_commit_absent_from_main_is_rejected(tmp_path):
    repository = _repository(tmp_path)
    _git(repository, "checkout", "-q", "-b", "topic")
    (repository / "marker.txt").write_text("topic\n", encoding="utf-8")
    _git(repository, "commit", "-q", "-am", "topic")
    topic = _git(repository, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DraftReleaseError, match="absent from"):
        assert_commit_is_on_main(topic, "main", cwd=repository)


def test_tag_commit_on_main_is_accepted(tmp_path):
    repository = _repository(tmp_path)
    commit = _git(repository, "rev-parse", "HEAD").stdout.strip()
    assert_commit_is_on_main(commit, "main", cwd=repository)


def test_dist_layout_requires_one_wheel_and_one_sdist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "aef-1.2.0-py3-none-any.whl").write_bytes(b"wheel")
    with pytest.raises(DraftReleaseError, match="exactly one wheel and one sdist"):
        locate_release_artifacts(dist)
    (dist / "aef-1.2.0.tar.gz").write_bytes(b"sdist")
    wheel, sdist = locate_release_artifacts(dist)
    assert wheel.name.endswith(".whl")
    assert sdist.name.endswith(".tar.gz")


def test_checksums_use_canonical_sorted_gnu_format(tmp_path):
    first = tmp_path / "b-file.txt"
    second = tmp_path / "a-file.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    rendered = render_sha256sums((first, second))
    records = parse_sha256sums(rendered)
    assert list(records) == ["a-file.txt", "b-file.txt"]
    assert records["a-file.txt"] == hashlib.sha256(b"two").hexdigest()
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


@pytest.mark.parametrize(
    "text",
    [
        "not-a-digest  file.txt\n",
        f"{'a' * 64} file.txt\n",
        f"{'a' * 64}  dir/file.txt\n",
        f"{'a' * 64}  b.txt\n{'a' * 64}  a.txt\n",
        f"{'a' * 64}  file.txt\n\n",
        f"{'a' * 64}  file.txt",
    ],
)
def test_checksums_reject_non_canonical_format(text):
    with pytest.raises(DraftReleaseError, match="canonical form"):
        parse_sha256sums(text)


def test_write_sha256sums_covers_wheel_and_sdist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "aef-1.2.0-py3-none-any.whl"
    sdist = dist / "aef-1.2.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    checksums = write_sha256sums(dist, (wheel, sdist))
    assert checksums.name == "SHA256SUMS.txt"
    records = parse_sha256sums(checksums.read_text(encoding="ascii"))
    assert set(records) == {wheel.name, sdist.name}


class _FakeResponse:
    def __init__(self, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GitHubDraftTag404:
    """GitHub's /releases/tags/{tag} endpoint omits drafts."""

    def __init__(self):
        self.next_id = 11
        self.releases: dict[int, dict] = {}

    def __call__(self, request, timeout=60):
        method = request.get_method()
        url = request.full_url
        if method == "GET" and "/releases/tags/" in url:
            raise HTTPError(
                url,
                404,
                "Not Found",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"Not Found"}'),
            )
        if method == "GET" and "/releases?" in url:
            return _FakeResponse(list(self.releases.values()))
        if method == "GET" and "/releases/" in url:
            release_id = int(url.rsplit("/", 1)[1])
            record = self.releases.get(release_id)
            if record is None:
                raise HTTPError(
                    url,
                    404,
                    "Not Found",
                    hdrs=None,
                    fp=io.BytesIO(b'{"message":"Not Found"}'),
                )
            return _FakeResponse(record)
        if method == "POST" and url.endswith("/releases"):
            body = json.loads(request.data.decode("utf-8"))
            record = {
                "id": self.next_id,
                "tag_name": body["tag_name"],
                "name": body["name"],
                "draft": True,
                "html_url": f"https://example.test/{body['tag_name']}",
                "upload_url": (
                    f"https://uploads.example.test/{self.next_id}/assets{{?name,label}}"
                ),
                "body": body["body"],
                "assets": [],
            }
            self.releases[self.next_id] = record
            self.next_id += 1
            return _FakeResponse(record)
        if method == "POST" and "/assets" in url:
            name = url.split("name=", 1)[1]
            content = request.data
            record = next(iter(self.releases.values()))
            asset = {
                "name": name,
                "size": len(content),
                "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                "url": f"https://example.test/assets/{name}",
            }
            record["assets"].append(asset)
            return _FakeResponse(asset)
        raise AssertionError(f"unexpected request {method} {url}")


def test_github_draft_survives_tag_endpoint_404():
    from scripts.prepare_draft_release import GitHubReleases

    opener = GitHubDraftTag404()
    client = GitHubReleases(
        token="ghp_test_token",
        repository="webdigit/agent-evolution-framework",
        api_url="https://api.example.test",
        opener=opener,
    )
    desired = _desired()
    release, plan = apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    assert plan.action == "create"
    assert release.draft is True
    assert release.release_id == 11
    assert {asset.name for asset in release.assets} == {asset.name for asset in desired}

    reused, retry = apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    assert retry.action == "reuse"
    assert reused.release_id == 11
    assert len(opener.releases) == 1


def test_multiple_releases_for_the_same_tag_fail_closed():
    from scripts.prepare_draft_release import GitHubReleases

    opener = GitHubDraftTag404()
    first = {
        "id": 1,
        "tag_name": "v1.2.0",
        "name": "AEF 1.2.0",
        "draft": True,
        "html_url": "https://example.test/one",
        "body": render_release_body(tag="v1.2.0", commit=COMMIT_A, version="1.2.0"),
        "assets": [],
    }
    opener.releases = {1: first, 2: {**first, "id": 2, "html_url": "https://example.test/two"}}
    client = GitHubReleases(
        token="ghp_test_token",
        repository="webdigit/agent-evolution-framework",
        api_url="https://api.example.test",
        opener=opener,
    )
    with pytest.raises(DraftReleaseError, match="multiple Releases share this tag"):
        client.get_release_by_tag("v1.2.0")


def test_simulated_draft_release_creation_uploads_exact_assets():
    client = MemoryGitHub()
    desired = _desired()
    release, plan = apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    assert plan.action == "create"
    assert release.draft is True
    assert {asset.name for asset in release.assets} == {asset.name for asset in desired}
    assert client.deleted == []
    assert client.published == []


def test_identical_retry_reuses_draft_without_duplicates():
    client = MemoryGitHub()
    desired = _desired()
    apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    release, plan = apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    assert plan.action == "reuse"
    assert plan.uploads == ()
    assert len(release.assets) == 3
    assert client.deleted == []


def test_divergent_assets_fail_closed_without_deletion():
    desired = _desired()
    existing = ExistingRelease(
        tag="v1.2.0",
        name="AEF 1.2.0",
        draft=True,
        html_url="https://example.test/v1.2.0",
        release_id=1,
        commit=COMMIT_A,
        assets=(
            ExistingAsset(
                name="aef-1.2.0-py3-none-any.whl",
                sha256="0" * 64,
                size=5,
            ),
        ),
    )
    with pytest.raises(DraftReleaseError, match="divergent asset"):
        plan_draft_release(
            existing, desired, tag="v1.2.0", commit=COMMIT_A, version="1.2.0"
        )


def test_already_published_release_fails_closed():
    desired = _desired()
    existing = ExistingRelease(
        tag="v1.2.0",
        name="AEF 1.2.0",
        draft=False,
        html_url="https://example.test/v1.2.0",
        release_id=1,
        commit=COMMIT_A,
        assets=(),
    )
    with pytest.raises(DraftReleaseError, match="already published"):
        plan_draft_release(
            existing, desired, tag="v1.2.0", commit=COMMIT_A, version="1.2.0"
        )


def test_release_workflow_uses_minimal_permissions():
    assert "contents: write" in WORKFLOW
    assert "releases:" not in WORKFLOW
    assert "contents: read" not in WORKFLOW
    assert "concurrency:" in WORKFLOW
    assert "aef-draft-release-" in WORKFLOW


def test_release_workflow_never_publishes_or_moves_tags():
    assert "draft: false" not in WORKFLOW
    assert "--draft=false" not in WORKFLOW
    assert "gh release" not in WORKFLOW
    assert "git tag" not in WORKFLOW
    assert "git push --force" not in WORKFLOW
    assert '"draft": True' in SCRIPT
    assert '"draft": False' not in SCRIPT
    assert "DELETE" not in SCRIPT
    assert "scripts/reproducible_build.py" in WORKFLOW
    assert "requirements-release.txt" in WORKFLOW
    assert "--no-isolation" in BUILD_SCRIPT
    assert "python -m build --no-isolation" in CI
    assert "SOURCE_DATE_EPOCH" in (ROOT / "scripts/reproducible_build.py").read_text(
        encoding="utf-8"
    )
    assert "python -m twine check" in WORKFLOW
    assert "check-wheel-contents dist/*.whl" in WORKFLOW
    assert "scripts/verify_artifacts.py" in WORKFLOW
    assert 'aef" --version' in WORKFLOW
    assert 'python" -m aef --version' in WORKFLOW
    assert "workflow_dispatch" in WORKFLOW


def test_redact_secrets_never_prints_tokens():
    token = "ghp_super_secret_token"
    redacted = redact_secrets(f"Authorization: Bearer {token}", token)
    assert token not in redacted
    assert "Bearer [redacted]" in redacted


def test_github_errors_redact_token_from_response_body():
    token = "ghp_super_secret_token"

    def opener(request, timeout=60):
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(f'{{"message":"{token}"}}'.encode("utf-8")),
        )

    from scripts.prepare_draft_release import GitHubReleases

    client = GitHubReleases(
        token=token,
        repository="webdigit/agent-evolution-framework",
        opener=opener,
    )
    with pytest.raises(DraftReleaseError, match="401") as error:
        client.get_release_by_tag("v1.2.0")
    assert token not in str(error.value)


def test_operator_summary_requires_human_publish():
    desired = _desired()
    summary = operator_summary(
        tag="v1.2.0",
        commit="abc123",
        version="1.2.0",
        validations=("twine_check",),
        assets=desired,
        draft_release_url="https://example.test/v1.2.0",
        plan=plan_draft_release(
            None, desired, tag="v1.2.0", commit=COMMIT_A, version="1.2.0"
        ),
    )
    assert summary["human_action_required"] == "publish_release"
    assert summary["tag"] == "v1.2.0"
    assert json.dumps(summary)


def test_version_from_wheel_reads_metadata(tmp_path):
    wheel = tmp_path / "agent_evolution_framework-1.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "agent_evolution_framework-1.2.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: agent-evolution-framework\nVersion: 1.2.0\n",
        )
    assert version_from_wheel(wheel) == "1.2.0"


def test_two_clean_builds_of_the_same_commit_are_byte_identical(tmp_path):
    epoch = source_date_epoch("HEAD", cwd=ROOT)
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_once(first, epoch=epoch, cwd=ROOT)
    build_once(second, epoch=epoch, cwd=ROOT)
    assert_identical_release_builds(first, second)
    left_wheel, left_sdist = locate_release_artifacts(first)
    right_wheel, right_sdist = locate_release_artifacts(second)
    assert left_wheel.stat().st_size > 0
    assert left_sdist.stat().st_size > 0
    assert (ROOT / "requirements-release.txt").read_text(
        encoding="utf-8"
    ).splitlines() == RELEASE_PINS


def test_release_backend_is_pinned_and_builds_without_isolation():
    pins = (ROOT / "requirements-release.txt").read_text(encoding="utf-8").splitlines()
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert pins == RELEASE_PINS
    assert 'requires = ["setuptools==84.0.0"]' in project
    assert "setuptools>=" not in project
    command = release_build_command(Path("dist"))
    assert command[1:4] == ["-m", "build", "--no-isolation"]
    assert "--isolation" not in command


def test_isolated_backend_install_is_rejected():
    with pytest.raises(DraftReleaseError, match="floating backend"):
        assert_no_isolated_dependency_install(
            "* Creating isolated environment: venv+pip...\n"
            "* Installing packages in isolated environment:\n"
            "  - setuptools>=77\n",
            "",
        )
    assert_no_isolated_dependency_install(
        "* Getting build dependencies for sdist...\n* Building sdist...\n",
        "",
    )


def test_release_attribution_is_canonical_and_commit_bound():
    body = render_release_body(tag="v1.2.0", commit=COMMIT_A, version="1.2.0")
    payload = parse_attribution_body(body)
    assert payload["commit"] == COMMIT_A
    assert payload["tag"] == "v1.2.0"
    assert payload["name"] == "AEF 1.2.0"
    assert payload["schema"] == "aef.release.attribution/v1"


def test_partial_draft_then_moved_tag_fails_closed():
    client = MemoryGitHub()
    desired = _desired()
    apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    record = client.releases["v1.2.0"]
    record["assets"] = [
        asset for asset in record["assets"] if asset["name"] == "SHA256SUMS.txt"
    ]
    with pytest.raises(DraftReleaseError, match="moved tag"):
        apply_draft_release(
            client, tag="v1.2.0", version="1.2.0", commit=COMMIT_B, desired=desired
        )
    assert [asset["name"] for asset in record["assets"]] == ["SHA256SUMS.txt"]
    assert client.deleted == []


def test_partial_draft_same_commit_completes_missing_assets():
    client = MemoryGitHub()
    desired = _desired()
    apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    record = client.releases["v1.2.0"]
    record["assets"] = [
        asset for asset in record["assets"] if asset["name"] == "SHA256SUMS.txt"
    ]
    release, plan = apply_draft_release(
        client, tag="v1.2.0", version="1.2.0", commit=COMMIT_A, desired=desired
    )
    assert plan.action == "complete"
    assert {asset.name for asset in release.assets} == {asset.name for asset in desired}
    assert client.deleted == []


def test_existing_release_identity_mismatch_fails_closed():
    desired = _desired()
    existing = ExistingRelease(
        tag="v1.2.0",
        name="Wrong name",
        draft=True,
        html_url="https://example.test/v1.2.0",
        release_id=1,
        commit=COMMIT_A,
        assets=(),
    )
    with pytest.raises(DraftReleaseError, match="release name"):
        plan_draft_release(
            existing, desired, tag="v1.2.0", commit=COMMIT_A, version="1.2.0"
        )


def test_release_documentation_covers_gates_and_recovery():
    assert "docs/release.md" in README
    for fragment in (
        "three human gates",
        "GO merge",
        "GO tag",
        "GO publish",
        "vX.Y.Z",
        "workflow_dispatch",
        "human_action_required: publish_release",
        "SHA-256",
        "draft Release",
        "recovery",
        "SOURCE_DATE_EPOCH",
        "AEF_RELEASE_ATTRIBUTION",
        "moved tag",
        "--no-isolation",
        "setuptools",
        "tag lookup omits drafts",
        "release ID",
    ):
        assert fragment in DOCS
