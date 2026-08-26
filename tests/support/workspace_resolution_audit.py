"""AST helpers for workspace-resolution regression guards (tests only)."""

from __future__ import annotations

import ast

from aef.workspace_resolution import (
    CLI_WORKSPACE_CONSUMER_FUNCTIONS,
    CLI_WORKSPACE_NON_CONSUMER_FUNCTIONS,
)

_PATH_CONSTRUCTOR_NAMES = frozenset({"Path"})
_PATH_RESOLVER_NAMES = frozenset(
    {
        "abspath",
        "realpath",
        "expanduser",
        "normpath",
    }
)


def _is_getattr_workspace(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    is_getattr = (
        (isinstance(func, ast.Name) and func.id == "getattr")
        or (isinstance(func, ast.Attribute) and func.attr == "getattr")
    )
    if not is_getattr:
        return False
    if len(node.args) != 2:
        return False
    second = node.args[1]
    return isinstance(second, ast.Constant) and second.value == "workspace"


def _is_args_workspace(node: ast.expr) -> bool:
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "args"
            and node.attr == "workspace"
        )
    return _is_getattr_workspace(node)


def _function_references_args_workspace(function: ast.FunctionDef) -> bool:
    for child in ast.walk(function):
        if isinstance(child, ast.Attribute):
            if (
                isinstance(child.value, ast.Name)
                and child.value.id == "args"
                and child.attr == "workspace"
            ):
                return True
        if isinstance(child, ast.Call) and _is_getattr_workspace(child):
            return True
    return False


def list_functions_referencing_args_workspace(source: str) -> frozenset[str]:
    tree = ast.parse(source)
    return frozenset(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and _function_references_args_workspace(node)
    )


def audit_cli_workspace_resolution_registries(source: str) -> list[str]:
    """Return registry gaps for every cli.py function that references args.workspace."""
    referencing = list_functions_referencing_args_workspace(source)
    registered = CLI_WORKSPACE_CONSUMER_FUNCTIONS | CLI_WORKSPACE_NON_CONSUMER_FUNCTIONS
    missing = sorted(referencing - registered)
    if not missing:
        return []
    return [
        (
            f"{name} references args.workspace but is missing from "
            "CLI_WORKSPACE_CONSUMER_FUNCTIONS and "
            "CLI_WORKSPACE_NON_CONSUMER_FUNCTIONS"
        )
        for name in missing
    ]


class _WorkspaceBypassVisitor(ast.NodeVisitor):
    def __init__(self, function_name: str) -> None:
        self.function_name = function_name
        self.derived_names: set[str] = set()
        self.violations: list[str] = []

    def _mark_derived(self, names: list[str]) -> None:
        self.derived_names.update(names)

    def _expr_derives_from_workspace(self, node: ast.expr) -> bool:
        if _is_args_workspace(node):
            return True
        if isinstance(node, ast.Call) and _is_getattr_workspace(node):
            return True
        if isinstance(node, ast.Name) and node.id in self.derived_names:
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in self.derived_names:
                return True
            if isinstance(func, ast.Attribute) and func.attr in self.derived_names:
                return True
        return False

    def _record_violation(self, node: ast.AST, detail: str) -> None:
        line = getattr(node, "lineno", "?")
        self.violations.append(f"{self.function_name}:{line}: {detail}")

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._expr_derives_from_workspace(node.value):
            targets = [
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
            self._mark_derived(targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and self._expr_derives_from_workspace(node.value):
            if isinstance(node.target, ast.Name):
                self._mark_derived([node.target.id])
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in _PATH_CONSTRUCTOR_NAMES:
            for arg in node.args:
                if self._expr_derives_from_workspace(arg):
                    self._record_violation(
                        node,
                        f"{func.id}(...) resolves workspace outside the helper",
                    )
        if isinstance(func, ast.Attribute):
            if func.attr in _PATH_RESOLVER_NAMES:
                for arg in node.args:
                    if self._expr_derives_from_workspace(arg):
                        self._record_violation(
                            node,
                            f"{func.attr}(...) resolves workspace outside the helper",
                        )
            if func.attr in {"resolve", "absolute", "expanduser"}:
                if self._expr_derives_from_workspace(func.value):
                    self._record_violation(
                        node,
                        f".{func.attr}() resolves workspace outside the helper",
                    )
        self.generic_visit(node)


def find_workspace_resolution_bypasses(source: str) -> list[str]:
    """
    Flag module-level functions that resolve paths from args.workspace outside the helper.

    Anti-regression guard, not an exhaustive proof. Limitations:
    - derivation is not tracked through intermediate calls (e.g. ws = str(args.workspace)
      then Path(ws) is not reported);
    - only module-level function bodies are scanned, not nested closures.
    """
    tree = ast.parse(source)
    violations: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        visitor = _WorkspaceBypassVisitor(node.name)
        for child in node.body:
            visitor.visit(child)
        violations.extend(visitor.violations)
    return violations
