<!-- AEF:CLAUDE-PROJECT:BEGIN version="1.0.0" -->
# AEF Project Guidance

@../.agent/core/constitution.md
@../.agent/core/autonomy.md
@../.agent/core/learning.md
@../.agent/core/levels.md
@../.agent/core/scoring.md

- AEF applies only to this project. Do not infer AEF state, policy, authority, or doctrine from a parent directory, a user directory, or another project.
- Read and follow the imported AEF doctrine as project guidance.
- Treat Python policies, persisted JSON state, and approved hooks as the executable authorities. This file is guidance, not complete technical enforcement.
- Never present learned knowledge, a recommendation, a competency level, XP, or Trust as permission to perform an action.
- When an AEF result reports new promotion recommendations, notify the user.
- When review is required, use `aef evaluate --list` for human-readable consultation or `aef --json evaluate --list` for automation.
- Never approve, reject, refresh, or recover an evaluation on the user's behalf.
- If AEF reports that evaluation recovery is required, treat all affected levels as unavailable, notify the user, and do not perform recovery without an explicit user request.
- Use explicit `--json` output for automation. Preserve human output when presenting results to the user.
- Do not claim that AEF technically controls every Claude action unless an executable integration explicitly enforces that action.
<!-- AEF:CLAUDE-PROJECT:END -->
