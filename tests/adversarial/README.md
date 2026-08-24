# Adversarial banc

These scripts attack AEF **from the outside**: they drive the real CLI against
throwaway workspaces, under concurrency, under load, and against hostile
content. They complement the `pytest` suite — they do not replace it.

The distinction matters. The suite checks that the code does what we expect on
the paths we planned. This banc checks that the **guarantees still hold**
against what was not planned: a cloned repository with outbound links or a
trapped archive, eight concurrent processes, an interrupt in the middle of a
write, a read-only file, a marker quoted inside a code fence.

pytest does not collect this directory (`norecursedirs = adversarial`).

## Usage

Cross-platform, Windows and POSIX.

Detached worktree (measure a SHA):

```powershell
python 00-setup.py <SHA>
$env:AEF_BUILD='C:\Temp\audit-<SHA>'; python lance-tout.py
```

```bash
python3 00-setup.py <SHA>
AEF_BUILD=/tmp/audit-<SHA> python3 lance-tout.py
```

Current tree (CI: the checkout **is** the tree):

```bash
python3 00-setup.py --current
AEF_BUILD="$(git rev-parse --show-toplevel)" python3 lance-tout.py
```

`00-setup.py` creates the venv (`.venv\Scripts` or `.venv/bin` depending on the
OS), installs the package editable, and **stops immediately if the imported
tree is not the tree being measured**. Every script replays that check at
startup, via `bancenv.verifier_arbre_importe()`.

Scripts that require POSIX — `mkfifo`, symbolic links, `SIGKILL`, `strace` —
announce **« IGNORE on Windows »** (exit 77) rather than return a success that
is not one. On Windows, `05`, `07` and `10` therefore remain a Linux runner's
job.

Each script also runs on its own, with the Python of the measured venv.

On GitHub Actions, the `adversarial.yml` workflow runs on Linux, nightly, on
demand, or when a pull request carries the `adversarial` label. It is not
wired to every push.

## What each script proves

| Script | Property |
|---|---|
| `01-concurrence-ingest.py` | no lost write is reported as success (8, 16 and 32 processes) |
| `02-concurrence-declare.py` | the same property on the transactional path |
| `03-concurrence-record.py` | under contention, an explicit block rather than a silent queue |
| `04-plafond-evidences.py` | at the evidence cap, an explicit block rather than a misleading `NO_CHANGE` |
| `05-dryrun-vs-apply.py` *(POSIX)* | `--dry-run` yields the same verdict as apply, for seven journal states |
| `06-taux-erreur-fs.py` | a legitimate concurrent command does not return a filesystem error |
| `07-crash-sigkill.py` *(POSIX)* | an ordinary interrupt never leaves the workspace unrecoverable |
| `08-audit-scopage.py` | audit is scoped: an inherited workspace and a legitimate promotion stay `PASS` |
| `09-collision-identifiants.py` | crossed mutual guards; identifier collision is case × normalization |
| `10-epic3-runtime.py` *(POSIX)* | no repository binary executed, no network access, no zip amplification |
| `11-hygiene-git.py` | the runtime lock appears neither in `git status` nor in history |
| `12-fence-marqueurs.py` | a marker inside a Markdown fence is not a marker |
| `13-guidance-integrite.py` | blocked aggregate is atomic, file mode is preserved, no mid-flight overwrite |
| `14-ecrivain-externe.py` | a non-AEF thread rewriting a governed file is not overwritten behind a reported CHANGE |
| `decompte.py <before> <after>` | exact decomposition of the pytest delta between two worktrees |

## Three method rules

They are not decorative: each comes from a case where a measurement looked
conclusive and was not.

**A positive control before any conclusion.** A scenario that does not
discriminate proves nothing. "Zero successful attacks" can mean "the attacked
path was never reached". Each script therefore includes a case that **must**
succeed, and a case that **must** fail.

**Provoke the state rather than simulate it.** A test that hand-builds a
transaction journal does not see the recovery defect; a test that mocks a
declared size does not see zip amplification. Both can stay green while the
defect is open.

**Compare with the previous version before attributing a defect.** A surprising
behaviour is not necessarily a regression: check that it did not already exist.

## Exit codes

A script exits `0` if and only if every property it checks holds.

A script exits `77` when it **IGNORE**s the measurement because the platform
cannot support it. `lance-tout.py` counts that state as ignored, never as a
success.

Any other non-zero code means at least one property does not hold.
`lance-tout.py` aggregates, **names** the failing scripts, and itself exits
non-zero.
