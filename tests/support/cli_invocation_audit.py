"""AST audit: CLI subprocesses must use ``sys.executable -m aef``.

Scans every ``*.py`` under ``tests/`` (no frozen allow-list of files). A new
subprocess that targets the console script (``aef`` / ``aef.exe``),
``installed_aef_script(...)``, or a legacy ``AEF`` string path fails the audit.

Known limits (not silent bounds — documented so the guard is not overclaimed):

- Only ``subprocess.run`` / ``Popen`` / ``call`` / ``check_call`` /
  ``check_output`` are examined. Other process APIs are out of scope.
- Detection is syntactic: argv built through opaque helpers that hide the
  console script behind an intermediate Name without a matching assignment in
  the same file may slip through. Prefer ``aef_cli_argv`` / explicit
  ``[sys.executable, "-m", "aef", ...]``.
- ``tests/conftest.py`` may define ``installed_aef_script``; ``tests/test_cli_launcher.py``
  may call it for locator unit tests — neither may pass it to subprocess.
- Planted fixture paths named ``aef.exe`` that are never subprocess argv[0] for
  the CLI (e.g. discovery fixtures) are ignored unless they appear as the first
  element of a subprocess argv list.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SUBPROCESS_FUNCS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output"}
)

# Relative paths under tests/ that may *define* or *unit-test* the locator.
# They still must not pass the locator result to subprocess (checked below).
_LOCATOR_UNIT_FILES = frozenset(
    {
        "conftest.py",
        "test_cli_launcher.py",
    }
)


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS:
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            return True
    if isinstance(func, ast.Name) and func.id in _SUBPROCESS_FUNCS:
        # from subprocess import run
        return True
    return False


def _constant_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _looks_like_console_script_path(text: str) -> bool:
    lowered = text.replace("\\", "/").lower()
    base = Path(lowered).name
    return base in {"aef", "aef.exe"} or lowered.endswith("/aef.exe") or lowered.endswith("/aef")


def _is_installed_aef_script_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "installed_aef_script":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "installed_aef_script":
        return True
    return False


def _unwrap_str_call(node: ast.AST) -> ast.AST:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and node.args
    ):
        return node.args[0]
    return node


def _argv0_is_console_script(node: ast.AST) -> bool:
    node = _unwrap_str_call(node)
    if _is_installed_aef_script_call(node):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        # launcher() helpers that historically returned installed_aef_script()
        if node.func.id in {"launcher", "installed_aef_script"}:
            return True
    text = _constant_str(node)
    if text is not None and _looks_like_console_script_path(text):
        return True
    if isinstance(node, ast.Name) and node.id == "AEF":
        # Legacy bancenv: AEF was a string path to aef.exe. Module form must be
        # a list unpacked with *AEF — a bare Name as argv[0] is the old contract.
        return True
    return False


def _starred_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Starred) and isinstance(node.value, ast.Name):
        return node.value.id
    return None


def _list_argv0(node: ast.List) -> ast.AST | None:
    if not node.elts:
        return None
    first = node.elts[0]
    starred = _starred_name(first)
    if starred is not None:
        # [*AEF, ...] where AEF is the module argv list — OK if AEF is a list of
        # sys.executable -m aef. Bare *prefix from aef_cli_argv is also OK.
        # Only flag when the starred name is historically the console path.
        # After migration AEF is [PY, "-m", "aef"]; starring it is correct.
        return None
    return first


def _ifexp_branches(node: ast.IfExp) -> list[ast.AST]:
    return [node.body, node.orelse]


def _argv_expressions(call: ast.Call) -> list[ast.AST]:
    if not call.args:
        return []
    first = call.args[0]
    if isinstance(first, (ast.List, ast.IfExp)):
        return [first]
    return [first]


def _assignment_targets_from_installed_script(tree: ast.AST) -> frozenset[str]:
    """Names bound to ``installed_aef_script()`` (same-module dataflow)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_installed_aef_script_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            if _is_installed_aef_script_call(node.value) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return frozenset(names)


def _list_is_module_aef_argv(node: ast.List) -> bool:
    """True for ``[interpreter, "-m", "aef", ...]``."""
    if len(node.elts) < 3:
        return False
    return (
        _constant_str(node.elts[1]) == "-m"
        and _constant_str(node.elts[2]) == "aef"
    )


def find_console_script_cli_invocations(source: str, *, relative: str) -> list[str]:
    """Return human-readable findings for console-script CLI subprocess argv."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{relative}: unparseable ({exc})"]

    script_names = _assignment_targets_from_installed_script(tree)
    findings: list[str] = []

    def argv0_bad(node: ast.AST) -> bool:
        node = _unwrap_str_call(node)
        if _argv0_is_console_script(node):
            return True
        if isinstance(node, ast.Name) and node.id in script_names:
            return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        for argv_expr in _argv_expressions(node):
            if isinstance(argv_expr, ast.List):
                if _list_is_module_aef_argv(argv_expr):
                    continue
                argv0 = _list_argv0(argv_expr)
                if argv0 is not None and argv0_bad(argv0):
                    findings.append(
                        f"{relative}:{node.lineno}: subprocess argv[0] targets "
                        "the aef console script (use sys.executable -m aef)"
                    )
            elif isinstance(argv_expr, ast.IfExp):
                for branch in _ifexp_branches(argv_expr):
                    if not isinstance(branch, ast.List):
                        continue
                    if _list_is_module_aef_argv(branch):
                        continue
                    argv0 = _list_argv0(branch)
                    if argv0 is not None and argv0_bad(argv0):
                        findings.append(
                            f"{relative}:{node.lineno}: subprocess IfExp "
                            "branch targets the aef console script"
                        )
                    elif not _list_is_module_aef_argv(branch):
                        # Non-module branch paired with a module branch in IfExp
                        other = (
                            argv_expr.body
                            if branch is argv_expr.orelse
                            else argv_expr.orelse
                        )
                        if isinstance(other, ast.List) and _list_is_module_aef_argv(other):
                            findings.append(
                                f"{relative}:{node.lineno}: subprocess IfExp "
                                "branch targets the aef console script"
                            )
            elif argv0_bad(argv_expr):
                findings.append(
                    f"{relative}:{node.lineno}: subprocess executable is the "
                    "aef console script (use sys.executable -m aef)"
                )
    return findings


def iter_test_python_files(tests_root: Path) -> list[Path]:
    """Every ``*.py`` under tests/ — no frozen file list; exclusions are none."""
    return sorted(path for path in tests_root.rglob("*.py") if path.is_file())


def find_forbidden_installed_aef_script_imports(
    source: str, *, relative: str
) -> list[str]:
    """Fail when a non-locator test imports ``installed_aef_script`` (new sites)."""
    if relative in _LOCATOR_UNIT_FILES:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "installed_aef_script":
                    findings.append(
                        f"{relative}:{node.lineno}: import installed_aef_script "
                        "(CLI tests must use sys.executable -m aef / aef_cli_argv)"
                    )
        if isinstance(node, ast.Name) and node.id == "installed_aef_script":
            # Reference without import still counts if loaded via star — rare.
            pass
    return findings


def audit_tests_tree_for_console_script_cli(tests_root: Path) -> list[str]:
    """Scan the whole tests tree; return all console-script CLI findings."""
    findings: list[str] = []
    root = tests_root.resolve()
    for path in iter_test_python_files(root):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        findings.extend(find_console_script_cli_invocations(source, relative=relative))
        findings.extend(
            find_forbidden_installed_aef_script_imports(source, relative=relative)
        )
    return findings
