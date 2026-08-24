"""Markdown code masking — fences, tildes, indent, unclosed."""

from aef.markdown_code import (
    catalog_match,
    classify_managed_markers,
    mask_markdown_code,
    place_managed_segment,
    unclosed_fence_start,
)
from aef.guidance_integration import (
    AGENTS_BEGIN_PREFIX,
    AGENTS_BYTES,
    AGENTS_END_MARKER,
    CLAUDE_ROOT_BYTES,
    GUIDANCE_VERSION,
)


def test_fenced_backticks_and_tildes_are_masked():
    quoted = b"```md\n" + AGENTS_BYTES + b"```\n"
    masked = mask_markdown_code(quoted)
    assert AGENTS_BEGIN_PREFIX not in masked
    quoted_tilde = b"~~~md\n" + AGENTS_BYTES + b"~~~\n"
    assert AGENTS_BEGIN_PREFIX not in mask_markdown_code(quoted_tilde)


def test_indented_four_spaces_and_tab_are_masked():
    indented = b"".join(b"    " + line + b"\n" for line in AGENTS_BYTES.splitlines())
    assert AGENTS_BEGIN_PREFIX not in mask_markdown_code(indented)
    tabbed = b"\t" + AGENTS_BEGIN_PREFIX + b"\n"
    assert AGENTS_BEGIN_PREFIX not in mask_markdown_code(tabbed)


def test_unclosed_fence_masks_remainder():
    data = b"# prose\n```\n" + AGENTS_BYTES
    assert unclosed_fence_start(data) is not None
    assert AGENTS_BEGIN_PREFIX not in mask_markdown_code(data)
    assert b"# prose" in mask_markdown_code(data)


def test_place_segment_inserts_before_unclosed_fence():
    existing = b"# docs\n```\nquoted\n"
    placed = place_managed_segment(existing, CLAUDE_ROOT_BYTES)
    assert placed.index(CLAUDE_ROOT_BYTES) < placed.index(b"```")
    assert placed.endswith(b"```\nquoted\n") or b"```\nquoted\n" in placed


def test_catalog_match_tolerates_missing_trailing_newline():
    assert catalog_match(AGENTS_BYTES, AGENTS_BYTES) == "installed"
    assert catalog_match(AGENTS_BYTES[:-1], AGENTS_BYTES) == "installed"
    assert catalog_match(AGENTS_BYTES.replace(b"guidance", b"authority", 1), AGENTS_BYTES) == "modified"


def test_classify_ignores_fenced_markers():
    quoted = b"See:\n```\n" + AGENTS_BYTES + b"```\n"
    inspection = classify_managed_markers(
        quoted,
        begin_prefix=AGENTS_BEGIN_PREFIX,
        end_marker=AGENTS_END_MARKER,
        catalog={GUIDANCE_VERSION: AGENTS_BYTES},
    )
    assert inspection["state"] == "absent"
