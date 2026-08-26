"""AST helpers for derived-identifier prefix regression guards (tests only)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from aef.identifiers import DERIVED_IDENTIFIER_PREFIXES

_DERIVED_ID_LITERAL_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*:")

# Digest condensates use DIGEST_PREFIX = "sha256:" (record_document.py), not a
# learning-derived namespace. Strip that prefix from the derived-id audit so a
# digest literal never looks like an undeclared learning namespace.
_EXCLUDED_DERIVED_LITERAL_PREFIXES = frozenset({"sha256:"})

# Files scanned by default: every src/aef/*.py. The entries below are the only
# exceptions — each is excluded because it contains colon strings that are not
# learning-derived identifier namespaces. Justified file by file.
# Guard: audit_excluded_files_remain_free_of_derived_prefixes fails if any of
# these files later contains a literal starting with DERIVED_IDENTIFIER_PREFIXES
# (then remove the file from this set and let the full registry audit cover it).
DERIVED_ID_AUDIT_EXCLUDED_FILES = frozenset(
    {
        # Human/stderr strings ("aef: …") and interactive evaluation labels.
        "src/aef/cli.py",
        # Ledger event ids ("competency-declaration:…"), not learning namespaces.
        "src/aef/competency_declaration.py",
        # Transaction ids ("competency-declaration-transaction:…").
        "src/aef/competency_declaration_transaction.py",
        # Transaction ids ("evaluation-transaction:…").
        "src/aef/evaluation_transaction.py",
        # Gate-reason labels ("trust:…"), not derived learning ids.
        "src/aef/progression.py",
        # Doctor observation tags and https:// URLs, not learning namespaces.
        "src/aef/runtime_doctor.py",
    }
)


def list_aef_module_relative_paths(root: Path) -> frozenset[str]:
    package = root / "src" / "aef"
    return frozenset(
        path.relative_to(root).as_posix()
        for path in package.glob("*.py")
        if path.is_file()
    )


def _derived_id_literal_segments(value: str) -> list[str]:
    if not _DERIVED_ID_LITERAL_PATTERN.search(value):
        return []
    if any(value.startswith(prefix) for prefix in _EXCLUDED_DERIVED_LITERAL_PREFIXES):
        return []
    return [value]


def list_derived_id_literals_in_source(source: str) -> frozenset[str]:
    """
    Collect string literals that look like ``namespace:…`` derived identifiers.

    Anti-regression guard, not an exhaustive proof. Limitations:
    - only captures prefixes written in clear text in string constants or
      f-string literal parts; a construction such as ``prefix = "signal"`` then
      ``f"{prefix}:{x}"`` is not reported;
    - only module-level AST walks of the given source text (no cross-module
      tracking of how prefixes are assembled).
    """
    tree = ast.parse(source)
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.update(_derived_id_literal_segments(node.value))
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    literals.update(_derived_id_literal_segments(part.value))
    return frozenset(literals)


def audit_derived_prefixes_in_source(
    source: str,
    registered: frozenset[str] = DERIVED_IDENTIFIER_PREFIXES,
) -> list[str]:
    """Return literals that look like derived id prefixes but match no registered namespace."""
    gaps: list[str] = []
    for literal in sorted(list_derived_id_literals_in_source(source)):
        if not any(literal.startswith(prefix) for prefix in registered):
            gaps.append(
                f"{literal!r} is not covered by DERIVED_IDENTIFIER_PREFIXES"
            )
    return gaps


def audit_derived_prefixes_in_repository(
    root: Path,
    registered: frozenset[str] = DERIVED_IDENTIFIER_PREFIXES,
    excluded_files: frozenset[str] = DERIVED_ID_AUDIT_EXCLUDED_FILES,
) -> list[str]:
    """Scan every ``src/aef/*.py`` except the explicit exclusion set."""
    gaps: list[str] = []
    for relative in sorted(list_aef_module_relative_paths(root)):
        if relative in excluded_files:
            continue
        source = (root / relative).read_text(encoding="utf-8")
        for item in audit_derived_prefixes_in_source(source, registered):
            gaps.append(f"{relative}: {item}")
    return gaps


def audit_excluded_files_remain_free_of_derived_prefixes(
    root: Path,
    registered: frozenset[str] = DERIVED_IDENTIFIER_PREFIXES,
    excluded_files: frozenset[str] = DERIVED_ID_AUDIT_EXCLUDED_FILES,
) -> list[str]:
    """
    Fail if an excluded file starts emitting a registered learning namespace.

    Exclusions are for non-learning colon strings only. The moment an excluded
    file contains a literal under DERIVED_IDENTIFIER_PREFIXES, the exclusion is
    no longer honest — remove the file from the exclusion set.
    """
    violations: list[str] = []
    for relative in sorted(excluded_files):
        path = root / relative
        if not path.is_file():
            violations.append(f"{relative}: excluded path is missing")
            continue
        source = path.read_text(encoding="utf-8")
        for literal in sorted(list_derived_id_literals_in_source(source)):
            if any(literal.startswith(prefix) for prefix in registered):
                violations.append(
                    f"{relative}: excluded file contains derived literal {literal!r}; "
                    "remove it from DERIVED_ID_AUDIT_EXCLUDED_FILES"
                )
    return violations
