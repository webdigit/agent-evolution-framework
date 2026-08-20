# Release delivery

AEF prepares a GitHub Release from a version tag. GitHub builds the artifacts
from its own checkout. The operator still authorizes merge, tag creation, and
publication. No other human copy-paste is required after the tag is pushed.

## Three human gates

These three human gates stay separate:

1. **GO merge** of the independently audited pull request.
2. **GO tag** of the merged, CI-green commit on `main` as `vX.Y.Z`.
3. **GO publish** of the verified draft Release.

A green CI run or an audit report is not authorization. The workflow never
merges, never creates or moves a tag, and never publishes a Release.

## Tag trigger

Pushing an annotated tag `vX.Y.Z` that points at a commit on `main` starts
`.github/workflows/release.yml`. The workflow:

1. checks out that tag, not a local operator tree;
2. verifies the tag is reachable from `origin/main`;
3. builds the wheel and sdist with `python -m build`;
4. runs `twine check`, `check-wheel-contents`, and
   `scripts/verify_artifacts.py`;
5. rebuilds a wheel from the sdist and verifies it;
6. installs the wheel in an isolated environment and checks
   `aef --version` and `python -m aef --version` against the tag;
7. writes canonical `SHA256SUMS.txt`;
8. creates or reuses a **draft Release** that contains exactly the wheel, the
   sdist, and the checksums.

Private paths such as `docs/prompts/`, `_bmad/`, `_bmad-output/`, `.agents/`,
and `.agent/` are refused in both archives.

## Retry mode

If the workflow is interrupted after the tag exists, rerun it with
`workflow_dispatch` and the same tag. The retry is idempotent when the assets
are identical. A published Release, a moved tag, or a divergent asset fails
closed. The workflow does not delete the Release or any existing asset.

## Reading the proofs

The job summary reports:

- tag, commit, and package version;
- the validations that passed;
- each asset name, size, and SHA-256;
- the draft Release URL;
- `human_action_required: publish_release`.

Download the three assets from the draft and recompute their hashes against
`SHA256SUMS.txt` before publication.

## Publish the draft

After the proofs are accepted, publish the GitHub Release manually. Then
download the published assets again and confirm the hashes still match. The
workflow never performs that publication.

## Recovery

Use this recovery table after a closed failure. Do not invent a substitute
tag, asset, or Release.

| Failure | Recovery |
| --- | --- |
| Malformed tag | Do not move the tag. Create the correct unused `vX.Y.Z` tag only after a new GO tag. |
| Tag commit absent from `main` | Do not retarget the tag. Merge the intended commit first, then tag that commit. |
| Package version differs from the tag | Stop. Correct the version on a new commit and start a new release candidate. |
| Private path or schema contract failure | Inspect the GitHub-built archives. Rebuild only from the tag checkout. |
| Divergent draft assets | Keep the existing draft. Investigate the mismatch. Do not delete assets. |
| Already published Release | Stop. Do not overwrite. Start a new version if a replacement is required. |
| Interrupted draft | Rerun `workflow_dispatch` for the same tag. Identical assets are reused. |
