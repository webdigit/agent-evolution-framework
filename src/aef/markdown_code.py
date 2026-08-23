"""Markdown code regions — fences and indented blocks (no I/O).

A marker inside a fenced or indented code region is not a marker. Detection
understands CommonMark-style ````` and ``~~~`` fences (0–3 spaces of indent,
optional info string) and four-space / tab indented code. An unclosed opening
fence treats the remainder of the document as code.
"""

from __future__ import annotations

from typing import Any


def _line_content(line: bytes) -> bytes:
    if line.endswith(b"\r"):
        return line[:-1]
    return line


def _opening_fence(content: bytes) -> tuple[bytes, int] | None:
    indent = 0
    while indent < len(content) and indent < 3 and content[indent:indent + 1] == b" ":
        indent += 1
    rest = content[indent:]
    if rest.startswith(b"```") or rest.startswith(b"~~~"):
        marker = rest[:1]
        length = 0
        while length < len(rest) and rest[length:length + 1] == marker:
            length += 1
        if length < 3:
            return None
        info = rest[length:]
        if marker == b"`" and b"`" in info:
            return None
        return marker, length
    return None


def _closing_fence(content: bytes, marker: bytes, length: int) -> bool:
    indent = 0
    while indent < len(content) and indent < 3 and content[indent:indent + 1] == b" ":
        indent += 1
    rest = content[indent:]
    if not rest.startswith(marker * 3):
        return False
    count = 0
    while count < len(rest) and rest[count:count + 1] == marker:
        count += 1
    if count < length:
        return False
    after = rest[count:]
    return after.strip(b" \t") == b""


def _indented_code(content: bytes) -> bool:
    if not content.strip():
        return False
    return content.startswith(b"    ") or content.startswith(b"\t")


def iter_markdown_lines(data: bytes):
    """Yield (offset, content_without_eol, end_offset_exclusive) for each line."""
    start = 0
    n = len(data)
    while start < n:
        newline = data.find(b"\n", start)
        if newline < 0:
            yield start, _line_content(data[start:]), n
            return
        yield start, _line_content(data[start:newline]), newline + 1
        start = newline + 1


def markdown_code_spans(data: bytes) -> list[tuple[int, int]]:
    """Return half-open byte ranges that are fenced or indented code."""
    spans: list[tuple[int, int]] = []
    fence: tuple[bytes, int] | None = None
    fence_start: int | None = None
    for offset, content, end in iter_markdown_lines(data):
        if fence is not None:
            if _closing_fence(content, fence[0], fence[1]):
                spans.append((offset if fence_start is None else fence_start, end))
                fence = None
                fence_start = None
            continue
        opened = _opening_fence(content)
        if opened is not None:
            fence = opened
            fence_start = offset
            continue
        if _indented_code(content):
            spans.append((offset, end))
    if fence is not None and fence_start is not None:
        spans.append((fence_start, len(data)))
    return spans


def mask_markdown_code(data: bytes) -> bytes:
    """Same-length copy with fenced and indented code replaced by spaces."""
    masked = bytearray(data)
    for start, end in markdown_code_spans(data):
        masked[start:end] = b" " * (end - start)
    return bytes(masked)


def unclosed_fence_start(data: bytes) -> int | None:
    """Byte offset of an opening fence that never closes, or None."""
    fence: tuple[bytes, int] | None = None
    fence_start: int | None = None
    for offset, content, _end in iter_markdown_lines(data):
        if fence is not None:
            if _closing_fence(content, fence[0], fence[1]):
                fence = None
                fence_start = None
            continue
        opened = _opening_fence(content)
        if opened is not None:
            fence = opened
            fence_start = offset
    return fence_start if fence is not None else None


def place_managed_segment(existing: bytes | None, segment: bytes) -> bytes:
    """Append *segment* in prose, inserting before an unclosed fence if needed."""
    if not existing:
        return segment
    insert_at = unclosed_fence_start(existing)
    if insert_at is None:
        return existing + b"\n\n" + segment
    if insert_at == 0:
        return segment + b"\n\n" + existing
    return existing[:insert_at] + b"\n\n" + segment + existing[insert_at:]


def catalog_match(captured: bytes, expected: bytes | None) -> str:
    """Return installed, modified, or unsupported_version."""
    if expected is None:
        return "unsupported_version"
    if captured == expected:
        return "installed"
    if expected.endswith(b"\n") and captured == expected[:-1]:
        return "installed"
    return "modified"


def classify_managed_markers(
    existing: bytes | None,
    *,
    begin_prefix: bytes,
    end_marker: bytes,
    catalog: dict[str, bytes],
) -> dict[str, Any]:
    """Classify one managed HTML-comment segment, ignoring markdown code."""
    if existing is None or existing == b"":
        return {"state": "absent", "version": None, "start": None, "end": None}
    prose = mask_markdown_code(existing)
    begin_count = prose.count(begin_prefix)
    end_count = prose.count(end_marker)
    if begin_count == 0 and end_count == 0:
        return {"state": "absent", "version": None, "start": None, "end": None}
    if begin_count != 1 or end_count != 1:
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    begin = prose.index(begin_prefix)
    end_pos = prose.index(end_marker)
    if end_pos < begin:
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    line_end = existing.find(b" -->", begin)
    if line_end < 0 or line_end > existing.find(b"\n", begin):
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    version_raw = existing[begin + len(begin_prefix):line_end]
    if len(version_raw) < 2 or version_raw[:1] != b'"' or version_raw[-1:] != b'"':
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    try:
        version = version_raw[1:-1].decode("ascii")
    except UnicodeDecodeError:
        return {"state": "ambiguous", "version": None, "start": None, "end": None}
    body_end = end_pos + len(end_marker)
    if existing[body_end:body_end + 1] == b"\n":
        body_end += 1
    start = begin
    if begin >= 2 and existing[begin - 2:begin] == b"\n\n":
        start = begin - 2
    state = catalog_match(existing[begin:body_end], catalog.get(version))
    return {"state": state, "version": version, "start": start, "end": body_end}
