"""Static guard: runtime doctor modules must not read workspace files directly."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aef.runtime_confined_io import RUNTIME_READ_GUARD_MODULES, RUNTIME_READ_SITES

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "aef"

FORBIDDEN_PATH_ATTRS = frozenset({
    "read_text",
    "read_bytes",
    "open",
    "stat",
    "read",
})

FORBIDDEN_CALLS = frozenset({
    "open",
})


def _line(source: str, node: ast.AST) -> str:
    if not hasattr(node, "lineno"):
        return ""
    return source.splitlines()[node.lineno - 1].strip()


def _collect_violations(module_path: Path) -> list[str]:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_PATH_ATTRS:
            violations.append(
                f"{module_path.name}:{node.lineno} attribute .{node.attr} "
                f"({_line(source, node)})",
            )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                violations.append(
                    f"{module_path.name}:{node.lineno} call {func.id}() "
                    f"({_line(source, node)})",
                )
    return violations


def test_runtime_modules_do_not_read_paths_directly():
    """Advisory syntax guard — behavior tests per site are authoritative (see M2)."""
    violations: list[str] = []
    for name in RUNTIME_READ_GUARD_MODULES:
        violations.extend(_collect_violations(SRC / name))
    assert not violations, (
        "direct workspace file reads are forbidden outside runtime_confined_io — "
        "use read_text_confined / read_bytes_confined / confined_file_size:\n"
        + "\n".join(violations)
    )


def test_runtime_read_sites_are_exhaustive():
    assert RUNTIME_READ_SITES == frozenset({
        "agent.runtime_requirements",
        "declared_env.pyvenv_cfg",
        "declared_env.version_file",
        "local_wheel.sha256",
        "checksum.sidecar",
        "dependency_wheel.archive",
    })


@pytest.mark.parametrize("site", sorted(RUNTIME_READ_SITES))
def test_runtime_read_site_names_are_stable(site: str):
    assert site == site.strip()
    assert " " not in site
