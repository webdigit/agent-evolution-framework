# AEF — agent guidance (repository)

This file is guidance for agents working in the agent-evolution-framework
repository. It is not permission, authority, or technical enforcement.

## Review conduct

When reviewing an implementation unit (story, lot point, or equivalent):

1. Classify **every** finding as either:
   - **blocking** — data loss, security failure, or a violated doctrinal contract;
   - **same-commit non-blocking** — docstring, comment, or wording that can be
     fixed in the current commit without opening a new review pass.
2. Cap review at **two passes** per work unit. After the second pass, commit what
   is ready and move remaining findings to a follow-up note — do not open a third
   pass on the same unit.
3. In the final report, list separately what review itself corrected (same-commit
   fixes), so the workflow's contribution stays measurable.
