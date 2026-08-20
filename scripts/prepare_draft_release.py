"""Prepare an idempotent GitHub draft Release from verified AEF artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence


TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA256SUMS_NAME = "SHA256SUMS.txt"
ATTRIBUTION_MARKER = "AEF_RELEASE_ATTRIBUTION"
ATTRIBUTION_SCHEMA = "aef.release.attribution/v1"


class DraftReleaseError(ValueError):
    """A closed failure while preparing a draft Release."""


@dataclass(frozen=True)
class DesiredAsset:
    name: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class ExistingAsset:
    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ExistingRelease:
    tag: str
    name: str
    draft: bool
    html_url: str
    release_id: int
    commit: str
    assets: tuple[ExistingAsset, ...]


@dataclass(frozen=True)
class ReleasePlan:
    action: str
    uploads: tuple[DesiredAsset, ...]


def redact_secrets(text: str, token: str) -> str:
    redacted = text
    if token:
        redacted = redacted.replace(token, "[redacted]")
    return re.sub(r"Bearer\s+\S+", "Bearer [redacted]", redacted)


def parse_release_tag(tag: str) -> str:
    if not TAG_PATTERN.fullmatch(tag):
        raise DraftReleaseError(f"malformed release tag: {tag}")
    return tag[1:]


def normalize_commit(commit: str) -> str:
    value = commit.strip().lower()
    if not HEX40.fullmatch(value):
        raise DraftReleaseError(f"release commit is invalid: {commit}")
    return value


def expected_release_name(version: str) -> str:
    return f"AEF {version}"


def attribution_payload(*, tag: str, commit: str, version: str) -> dict[str, str]:
    return {
        "commit": normalize_commit(commit),
        "name": expected_release_name(version),
        "schema": ATTRIBUTION_SCHEMA,
        "tag": tag,
        "version": version,
    }


def render_attribution_line(payload: dict[str, str]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def render_release_body(*, tag: str, commit: str, version: str) -> str:
    payload = attribution_payload(tag=tag, commit=commit, version=version)
    return (
        "Draft Release prepared by GitHub Actions.\n\n"
        "Verify the attached assets and SHA-256 checksums before publishing.\n"
        "human_action_required: publish_release\n"
        f"\n{ATTRIBUTION_MARKER}\n{render_attribution_line(payload)}\n"
    )


def parse_attribution_body(body: str) -> dict[str, str]:
    lines = body.splitlines()
    try:
        index = lines.index(ATTRIBUTION_MARKER)
    except ValueError as exc:
        raise DraftReleaseError("release attribution is missing") from exc
    if index + 1 >= len(lines):
        raise DraftReleaseError("release attribution is missing")
    raw = lines[index + 1]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DraftReleaseError("release attribution is invalid") from exc
    expected = {"commit", "name", "schema", "tag", "version"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise DraftReleaseError("release attribution is invalid")
    if payload.get("schema") != ATTRIBUTION_SCHEMA:
        raise DraftReleaseError("release attribution is invalid")
    commit = str(payload["commit"])
    normalize_commit(commit)
    if render_attribution_line({key: str(payload[key]) for key in sorted(payload)}) != raw:
        raise DraftReleaseError("release attribution is not canonical")
    return {key: str(payload[key]) for key in expected}


def assert_package_version_matches_tag(package_version: str, tag: str) -> None:
    expected = parse_release_tag(tag)
    if package_version != expected:
        raise DraftReleaseError(
            f"package version {package_version!r} does not match tag {tag!r}"
        )


def commit_is_on_ref(commit: str, ref: str, *, cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, ref],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def assert_commit_is_on_main(commit: str, main_ref: str, *, cwd: Path) -> None:
    if not commit_is_on_ref(commit, main_ref, cwd=cwd):
        raise DraftReleaseError(f"tag commit {commit} is absent from {main_ref}")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_sha256sums(paths: Sequence[Path]) -> str:
    lines = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(paths, key=lambda item: item.name)
    ]
    return "\n".join(lines) + "\n"


def parse_sha256sums(text: str) -> dict[str, str]:
    records: dict[str, str] = {}
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise DraftReleaseError("SHA256SUMS.txt is not in canonical form")
    for raw_line in text.splitlines():
        if not re.fullmatch(r"[0-9a-f]{64}  [^/\s]+", raw_line):
            raise DraftReleaseError("SHA256SUMS.txt is not in canonical form")
        digest, name = raw_line.split("  ", 1)
        if name in records:
            raise DraftReleaseError(f"duplicate checksum name: {name}")
        records[name] = digest
    if list(records) != sorted(records):
        raise DraftReleaseError("SHA256SUMS.txt is not in canonical form")
    return records


def version_from_wheel(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if PurePosixPath(name).name == "METADATA"
            and name.endswith(".dist-info/METADATA")
        ]
        if len(names) != 1:
            raise DraftReleaseError(f"wheel metadata is ambiguous in {path.name}")
        metadata = Parser().parsestr(archive.read(names[0]).decode("utf-8"))
    version = metadata.get("Version")
    if not version:
        raise DraftReleaseError(f"wheel is missing Version in {path.name}")
    return version


def locate_release_artifacts(dist: Path) -> tuple[Path, Path]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(
        path for path in dist.iterdir() if path.name.endswith(".tar.gz")
    )
    if len(wheels) != 1 or len(sdists) != 1:
        raise DraftReleaseError("dist must contain exactly one wheel and one sdist")
    return wheels[0], sdists[0]


def write_sha256sums(dist: Path, artifacts: Sequence[Path]) -> Path:
    checksums = dist / SHA256SUMS_NAME
    checksums.write_text(render_sha256sums(artifacts), encoding="ascii")
    parse_sha256sums(checksums.read_text(encoding="ascii"))
    return checksums


def load_desired_assets(paths: Sequence[Path]) -> tuple[DesiredAsset, ...]:
    assets = tuple(
        DesiredAsset(name=path.name, content=path.read_bytes())
        for path in sorted(paths, key=lambda item: item.name)
    )
    names = [asset.name for asset in assets]
    if len(names) != 3 or SHA256SUMS_NAME not in names:
        raise DraftReleaseError("draft Release requires wheel, sdist, and SHA256SUMS.txt")
    if not any(name.endswith(".whl") for name in names):
        raise DraftReleaseError("draft Release is missing the wheel")
    if not any(name.endswith(".tar.gz") for name in names):
        raise DraftReleaseError("draft Release is missing the sdist")
    return assets


def plan_draft_release(
    existing: ExistingRelease | None,
    desired: Sequence[DesiredAsset],
    *,
    tag: str,
    commit: str,
    version: str,
) -> ReleasePlan:
    wanted = tuple(desired)
    expected_name = expected_release_name(version)
    current_commit = normalize_commit(commit)
    parse_release_tag(tag)
    if existing is None:
        return ReleasePlan(action="create", uploads=wanted)
    if not existing.draft:
        raise DraftReleaseError("release is already published")
    if existing.tag != tag:
        raise DraftReleaseError(
            f"release tag {existing.tag!r} does not match {tag!r}"
        )
    if existing.name != expected_name:
        raise DraftReleaseError(
            f"release name {existing.name!r} does not match {expected_name!r}"
        )
    if existing.commit != current_commit:
        raise DraftReleaseError("moved tag")
    current = {asset.name: asset for asset in existing.assets}
    expected = {asset.name: asset for asset in wanted}
    extra = sorted(set(current) - set(expected))
    if extra:
        raise DraftReleaseError(f"divergent extra assets: {', '.join(extra)}")
    uploads: list[DesiredAsset] = []
    for name, asset in expected.items():
        present = current.get(name)
        if present is None:
            uploads.append(asset)
            continue
        if present.size != asset.size or present.sha256 != asset.sha256:
            raise DraftReleaseError(f"divergent asset: {name}")
        if not HEX64.fullmatch(present.sha256):
            raise DraftReleaseError(f"divergent asset: {name}")
    if uploads:
        return ReleasePlan(action="complete", uploads=tuple(uploads))
    return ReleasePlan(action="reuse", uploads=())


class GitHubReleases:
    def __init__(
        self,
        *,
        token: str,
        repository: str,
        api_url: str = "https://api.github.com",
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not token:
            raise DraftReleaseError("GITHUB_TOKEN is required")
        self._token = token
        self._repository = repository
        self._api_url = api_url.rstrip("/")
        self._opener = opener or urllib.request.urlopen

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "aef-draft-release",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        payload = data
        request_headers = self._headers(headers)
        if json_body is not None:
            payload = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=payload, method=method, headers=request_headers,
        )
        try:
            with self._opener(request, timeout=60) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    return json.loads(raw.decode("utf-8")) if raw else {}
                return raw
        except urllib.error.HTTPError as exc:
            detail = redact_secrets(
                exc.read().decode("utf-8", errors="replace"), self._token
            )
            raise DraftReleaseError(
                f"GitHub {method} failed: {exc.code} {detail}"
            ) from None

    def get_release(self, release_id: int) -> dict[str, Any] | None:
        url = f"{self._api_url}/repos/{self._repository}/releases/{int(release_id)}"
        return self._release_or_none(url)

    def get_release_by_tag(self, tag: str) -> dict[str, Any] | None:
        # GitHub's tag endpoint returns only published Releases. Drafts 404.
        published = self._release_or_none(
            f"{self._api_url}/repos/{self._repository}/releases/tags/"
            f"{urllib.parse.quote(tag)}"
        )
        if published is not None:
            return published
        matches = [
            payload
            for payload in self._iter_releases()
            if payload.get("tag_name") == tag
        ]
        if len(matches) > 1:
            raise DraftReleaseError("multiple Releases share this tag")
        if matches:
            return matches[0]
        return None

    def _release_or_none(self, url: str) -> dict[str, Any] | None:
        try:
            payload = self._request("GET", url)
        except DraftReleaseError as exc:
            if "failed: 404 " in str(exc):
                return None
            raise
        if not isinstance(payload, dict):
            raise DraftReleaseError("GitHub release payload is invalid")
        return payload

    def _iter_releases(self) -> Sequence[dict[str, Any]]:
        page = 1
        found: list[dict[str, Any]] = []
        while True:
            url = (
                f"{self._api_url}/repos/{self._repository}/releases"
                f"?per_page=100&page={page}"
            )
            payload = self._request("GET", url)
            if not isinstance(payload, list):
                raise DraftReleaseError("GitHub releases payload is invalid")
            if not payload:
                return found
            for item in payload:
                if not isinstance(item, dict):
                    raise DraftReleaseError("GitHub release payload is invalid")
                found.append(item)
            if len(payload) < 100:
                return found
            page += 1

    def create_draft_release(
        self, *, tag: str, version: str, commit: str
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"{self._api_url}/repos/{self._repository}/releases",
            json_body={
                "tag_name": tag,
                "name": expected_release_name(version),
                "draft": True,
                "prerelease": False,
                "generate_release_notes": False,
                "body": render_release_body(tag=tag, commit=commit, version=version),
            },
        )
        if not isinstance(payload, dict):
            raise DraftReleaseError("GitHub create-release payload is invalid")
        return payload

    def download_asset(self, asset_url: str) -> bytes:
        payload = self._request(
            "GET",
            asset_url,
            headers={"Accept": "application/octet-stream"},
        )
        if not isinstance(payload, (bytes, bytearray)):
            raise DraftReleaseError("GitHub asset download is invalid")
        return bytes(payload)

    def upload_asset(self, upload_url: str, asset: DesiredAsset) -> dict[str, Any]:
        base = upload_url.split("{", 1)[0]
        url = f"{base}?name={urllib.parse.quote(asset.name)}"
        payload = self._request(
            "POST",
            url,
            data=asset.content,
            headers={"Content-Type": "application/octet-stream"},
        )
        if not isinstance(payload, dict):
            raise DraftReleaseError("GitHub upload payload is invalid")
        return payload


def _asset_sha256(client: GitHubReleases, asset: dict[str, Any]) -> str:
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        value = digest.split(":", 1)[1]
        if HEX64.fullmatch(value):
            return value
    url = asset.get("url")
    if not isinstance(url, str) or not url:
        raise DraftReleaseError(f"release asset {asset.get('name')!r} has no digest")
    return sha256_bytes(client.download_asset(url))


def existing_release_from_payload(
    client: GitHubReleases,
    payload: dict[str, Any],
) -> ExistingRelease:
    assets = []
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            raise DraftReleaseError("release asset payload is invalid")
        name = asset.get("name")
        size = asset.get("size")
        if not isinstance(name, str) or not isinstance(size, int):
            raise DraftReleaseError("release asset payload is invalid")
        assets.append(
            ExistingAsset(name=name, sha256=_asset_sha256(client, asset), size=size)
        )
    attribution = parse_attribution_body(str(payload.get("body") or ""))
    tag_name = str(payload.get("tag_name") or "")
    name = str(payload.get("name") or "")
    if attribution["tag"] != tag_name:
        raise DraftReleaseError("release attribution does not match tag")
    if attribution["name"] != name:
        raise DraftReleaseError("release attribution does not match name")
    return ExistingRelease(
        tag=tag_name,
        name=name,
        draft=bool(payload.get("draft")),
        html_url=str(payload.get("html_url") or ""),
        release_id=int(payload.get("id")),
        commit=normalize_commit(attribution["commit"]),
        assets=tuple(assets),
    )


def apply_draft_release(
    client: GitHubReleases,
    *,
    tag: str,
    version: str,
    commit: str,
    desired: Sequence[DesiredAsset],
) -> tuple[ExistingRelease, ReleasePlan]:
    current_commit = normalize_commit(commit)
    payload = client.get_release_by_tag(tag)
    existing = existing_release_from_payload(client, payload) if payload else None
    plan = plan_draft_release(
        existing, desired, tag=tag, commit=current_commit, version=version
    )
    if plan.action == "create":
        created = client.create_draft_release(
            tag=tag, version=version, commit=current_commit
        )
        existing = existing_release_from_payload(client, created)
        payload = created
    if existing is None:
        raise DraftReleaseError("draft Release was not created")
    upload_url = str((payload or {}).get("upload_url") or "")
    if plan.uploads and not upload_url:
        raise DraftReleaseError("draft Release is missing an upload URL")
    for asset in plan.uploads:
        client.upload_asset(upload_url, asset)
    refreshed = client.get_release(existing.release_id)
    if refreshed is None:
        raise DraftReleaseError("draft Release disappeared after upload")
    return existing_release_from_payload(client, refreshed), plan


def operator_summary(
    *,
    tag: str,
    commit: str,
    version: str,
    validations: Sequence[str],
    assets: Sequence[DesiredAsset],
    draft_release_url: str,
    plan: ReleasePlan,
) -> dict[str, Any]:
    return {
        "tag": tag,
        "commit": commit,
        "version": version,
        "validations": list(validations),
        "assets": [
            {"name": asset.name, "size": asset.size, "sha256": asset.sha256}
            for asset in assets
        ],
        "draft_release_url": draft_release_url,
        "release_plan": plan.action,
        "human_action_required": "publish_release",
    }


def _write_step_summary(summary: dict[str, Any]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## Draft Release",
        "",
        f"- tag: `{summary['tag']}`",
        f"- commit: `{summary['commit']}`",
        f"- version: `{summary['version']}`",
        f"- validations: {', '.join(summary['validations'])}",
        f"- draft: {summary['draft_release_url']}",
        f"- human_action_required: `{summary['human_action_required']}`",
        "",
        "| Asset | Size | SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for asset in summary["assets"]:
        lines.append(
            f"| `{asset['name']}` | {asset['size']} | `{asset['sha256']}` |"
        )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--main-ref", required=True)
    parser.add_argument("--package-version", required=True)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--api-url", default="https://api.github.com")
    parser.add_argument("--validated", action="append", default=[])
    parser.add_argument("--cwd", default=Path.cwd(), type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        parse_release_tag(args.tag)
        current_commit = normalize_commit(args.commit)
        assert_package_version_matches_tag(args.package_version, args.tag)
        assert_commit_is_on_main(current_commit, args.main_ref, cwd=args.cwd)
        wheel, sdist = locate_release_artifacts(args.dist)
        wheel_version = version_from_wheel(wheel)
        assert_package_version_matches_tag(wheel_version, args.tag)
        checksums = write_sha256sums(args.dist, (wheel, sdist))
        desired = load_desired_assets((wheel, sdist, checksums))
        validations = list(args.validated) + ["checksums", "draft_release"]
        client = GitHubReleases(
            token=token,
            repository=args.repository,
            api_url=args.api_url,
        )
        release, plan = apply_draft_release(
            client,
            tag=args.tag,
            version=args.package_version,
            commit=current_commit,
            desired=desired,
        )
        summary = operator_summary(
            tag=args.tag,
            commit=current_commit,
            version=args.package_version,
            validations=validations,
            assets=desired,
            draft_release_url=release.html_url,
            plan=plan,
        )
    except DraftReleaseError as exc:
        print(redact_secrets(str(exc), token), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True))
    _write_step_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
