# AEF V1 Constitution

1. Explicit hard policies and constraints MUST take precedence over learned behavior, preferences, levels, Trust, and optimization goals.
2. An AEF operation returning BLOCKED MUST return its source state unchanged and its persistence adapter MUST apply no filesystem change.
3. An AEF operation returning FAILED MUST return its last explicitly defined safe state and MUST NOT expose a partial candidate as committed state.
4. Human approval MUST be an explicit input to every transition that requires it; absence of approval MUST NOT be interpreted as approval.
5. Identical state, inputs, policies, approvals, and injected identifiers or time SHOULD produce an identical transition.
6. Replay of an applied transition SHOULD return NO_CHANGE and MUST NOT duplicate durable records.
7. Unknown and project-owned data or files MUST be preserved unless an explicitly authorized operation owns their removal.
8. An agent MUST NOT infer or extend authority from capability, learning, success, global level, adjacent competency, or environmental access alone.
9. AEF activation MUST be local to the explicitly selected project workspace by default.
10. A project MUST NOT implicitly inherit AEF state, authority, policy, or doctrine from a parent directory, a user directory, ~/.claude/, or another project.
11. Filesystem mutations MUST remain confined to authorized paths below the selected workspace.
12. Any integration that executes real actions MUST enforce executable Python policies and approved hooks before execution.
13. Any integration receiving ALLOW_WITH_LOG MUST persist the required durable authorization record before executing the real action; otherwise it MUST block or escalate.
14. Python code and approved hooks are authoritative for executable policies and transitions; JSON is authoritative for persisted current state.
15. Core Markdown files are normative doctrine but MUST NOT be treated as executable policy by themselves.
16. This Constitution takes precedence over every other AEF core document when their interpretations conflict.
