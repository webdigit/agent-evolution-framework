# Concepts

## A runtime, not a harness plugin

AEF is a program that belongs to one project. It is not a marketplace plugin,
a hook pack, or a skill library loaded into Claude Code, Cursor, Codex, or any
other editor.

That distinction is the product. A harness plugin buys convenience inside one
tool: skills, memory files, and session instincts that the model *may* follow.
AEF buys guarantees that still hold when the editor changes and when the model
ignores the prompt. A rule the agent can talk its way around is not a rule.

Python is the vehicle, not the identity. The identity is a CLI that can
refuse: ingest is bounded, writes contend instead of queueing, equivalent input
replays as `NO_CHANGE`, and `EVALUATE` never promotes. The interpreter is the
court, `.agent/` is the docket, and the model is a party — not the judge.
Guidance (`AGENTS.md`, doorbells) points at that court. It does not replace it.

## Project-local state

AEF state belongs to one project and lives under `.agent/`. JSON is the
authority for current state, Python for calculations and transitions, and the
five core Markdown files for normative doctrine.

## Capability is not authority

Discovering a connector or demonstrating a competency does not authorize an
action. Effective autonomy remains constrained by global level, local
competency level, risk, Trust, probation, hard policy, and required approval.

## Learning lifecycle

Knowledge progresses conservatively from signal to observation to hypothesis
to rule to principle. A signal is not a rule, an isolated observation is not a
generalization, and principles require human approval. Evidence identifiers and
lifecycle history remain durable.

## Levels and promotion

New competencies start at L1. Career and competency levels are separate.
Official birth of a competency is a dedicated AEF declaration with a cited
record and a human decision — not a manual edit of `competencies.json`, and
not an EVALUATE promotion.
Outcomes may create durable pending recommendations, but tasks never promote
directly. EVALUATE recomputes readiness and requires an explicit human decision;
a promotion advances exactly one level.

## Cases, XP, Trust, and probation

`cases` counts evaluated cases. XP measures accumulated experience. Trust is a
separate signal; `null` means unproven, not zero. Complex exposure, recent
significant errors, and probation also affect readiness and permission. Python
remains authoritative for thresholds.

## Replay and preservation

Equivalent input should return `NO_CHANGE` without rewriting files. A blocked
or failed operation preserves source state. Unknown user-owned files and
allowed extensions are preserved; historical state is not silently migrated.
