"""Pure render of operational learned knowledge for integrate learning."""

from __future__ import annotations

from typing import Any

from .ingest_ops import KNOWLEDGE_PATH, MANIFEST_PATH
from .rule_lifecycle import applicable_rules


LEARNING_CARD_VERSION = "1.0.0"
LEARNING_CARD_HONESTY_MARKER = "Trust : declared ingest events only (not verified)"


class LearningCardBlockedError(Exception):
    """Raised when knowledge cannot be read for card render."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def knowledge_snapshot_from_project(project: dict[str, Any]) -> dict[str, Any]:
    """Extract persisted knowledge from an in-memory workspace snapshot."""
    files = project.get("files") if isinstance(project, dict) else None
    if not isinstance(files, dict):
        raise LearningCardBlockedError(
            "workspace_not_initialized",
            "the workspace is not an initialized AEF project.",
        )
    if MANIFEST_PATH not in files or KNOWLEDGE_PATH not in files:
        raise LearningCardBlockedError(
            "workspace_not_initialized",
            "the workspace is not an initialized AEF project.",
        )
    knowledge = files[KNOWLEDGE_PATH]
    if not isinstance(knowledge, dict):
        raise LearningCardBlockedError(
            "invalid_knowledge_state",
            "persisted knowledge is not a JSON object.",
        )
    return knowledge


def _active_principles(principles: list[Any]) -> list[dict[str, Any]]:
    active = [
        item for item in principles
        if isinstance(item, dict) and item.get("status", "active") == "active"
    ]
    return sorted(active, key=lambda item: item["id"])


def _format_bool(value: Any) -> str:
    return "true" if value else "false"


def _format_evidence_ids(evidence_ids: list[Any] | None) -> str:
    if not evidence_ids:
        return "—"
    return ", ".join(sorted(str(item) for item in evidence_ids))


def _format_rule(rule: dict[str, Any]) -> list[str]:
    rule_id = rule.get("id", "—")
    return [
        f"### `{rule_id}`",
        "",
        "- **Type**: `rule` — derived from declared evidence; revisable.",
        f"- **Pattern**: `{rule.get('pattern_key', '—')}`",
        f"- **Derived from**: `{rule.get('derived_from', '—')}`",
        f"- **Confirmations**: {rule.get('confirmations', 0)}",
        "- **Explicit human validation**: "
        f"{_format_bool(rule.get('explicit_human_validation', False))}",
        f"- **Evidence ids**: {_format_evidence_ids(rule.get('evidence_ids'))}",
        "- **Obligation**: operational guidance from declared ingest events —",
        "  subordinate to `.agent/core/` doctrine; not verified truth.",
        "",
    ]


def _format_principle(principle: dict[str, Any]) -> list[str]:
    principle_id = principle.get("id", "—")
    return [
        f"### `{principle_id}`",
        "",
        "- **Type**: `principle` — human-approved promotion from rule.",
        f"- **Derived from**: `{principle.get('derived_from', '—')}`",
        "- **Human approved**: true",
        "- **Obligation**: stronger than a rule alone; still subordinate to",
        "  `.agent/core/` doctrine.",
        "",
    ]


def render_learning_card(knowledge: dict[str, Any]) -> str:
    """Pure deterministic markdown body for the managed learning card.

    Same knowledge snapshot → same text. No filesystem I/O, no timestamps.
    A written card that diverges from a fresh render is **périmé** (stale),
    never catalog-tampered.
    """
    if not isinstance(knowledge, dict):
        raise ValueError("knowledge must be a JSON object")

    rules = applicable_rules(knowledge.get("rules") or [])
    principles = _active_principles(knowledge.get("principles") or [])

    lines = [
        "# Learned operational knowledge",
        "",
        "Snapshot of active rules and principles from persisted `knowledge.json`.",
        "It is not doctrine. Read `.agent/core/learning.md` for lifecycle rules",
        "and `.agent/core/constitution.md` for authority. Regenerate with",
        "`aef integrate learning`. When the file no longer matches knowledge,",
        "it is **périmé** (stale) — regenerate; do not treat divergence as",
        "catalog tampering.",
        "",
        LEARNING_CARD_HONESTY_MARKER,
        "",
        "## Source hierarchy",
        "",
        "- **Doctrine** (`.agent/core/learning.md`, constitution): written and",
        "  weighed product rules — highest authority for what learning means.",
        "- **`rule`** below: derived from declared evidence, revisable via",
        "  CONSOLIDATE or supersession — operational, not constitutional.",
        "- **`principle`**: promoted from an active rule with explicit human",
        "  approval — stronger than a rule alone, still below doctrine.",
        "",
    ]

    if not rules and not principles:
        lines.extend([
            "## Active rules and principles",
            "",
            "This workspace has **no active learned rules or principles** yet.",
            "Hypotheses and internal signals remain in",
            "`.agent/knowledge/knowledge.json` and are not copied here.",
            "",
        ])
    else:
        if rules:
            lines.extend(["## Active rules", ""])
            for rule in rules:
                lines.extend(_format_rule(rule))
        if principles:
            lines.extend(["## Active principles", ""])
            for principle in principles:
                lines.extend(_format_principle(principle))

    lines.extend([
        "## Refresh",
        "",
        "```console",
        "aef integrate learning",
        "```",
        "",
    ])
    return "\n".join(lines)


def wrap_learning_segment(card_body: str) -> bytes:
    """Wrap a rendered card body in LEARNING managed markers (UTF-8 bytes)."""
    body = card_body if card_body.endswith("\n") else f"{card_body}\n"
    return (
        f'<!-- AEF:LEARNING:BEGIN version="{LEARNING_CARD_VERSION}" -->\n'
        f"{body}"
        f"<!-- AEF:LEARNING:END -->\n"
    ).encode("utf-8")
