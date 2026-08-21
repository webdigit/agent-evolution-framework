# Rapport DEV consolidé — validation Epics 3–6

**Date :** 2026-08-21
**Branche :** `feature/epics-3-6`
**HEAD :** `e2a68f1d8f30dcf8303273f17d2a13128f09c318`
**SHA clôture précédent (amendé) :** `5d4b3d0dd4242f25a9603ad18df47a104d1beac5`
**Base :** `origin/main` — **ahead 6**
**Version verrouillée :** **1.2.0** (aucun bump)
**Push / PR :** non effectués
**Preuves :** `_bmad-output/implementation-artifacts/validation-epics-3-6-2026-08-21/`

## Pré-requis worktree

| Contrôle | Résultat |
|---|---|
| Inventaire exact avant stash | 27 fichiers ` M` (CRLF) + `delivery-pipeline-epic-2-…` (delta sémantique 7+/3−) ; aucun `??` |
| CRLF sans delta sémantique | Confirmé : `git diff` / `-w` / `--ignore-cr-at-eol` **EMPTY** pour tous sauf `delivery-pipeline-epic-2-…` |
| Stash nommé | `stash@{0}: pre-validation-epics-3-6-crlf-and-delivery-pipeline-epic-2` |
| Stash antérieur conservé | `stash@{1}: epics-3-6-full-worktree-from-release-prepare-1.2.0` |
| Worktree propre | Oui (`git status --porcelain` vide) |

## Correctif whitespace (amend clôture)

Défaut purement documentaire (trailing spaces) dans les six md de clôture Epic 5–6. Commit de clôture encore non poussé → **amend `--no-edit`** de `5d4b3d0` → **`e2a68f1`** (pas de commit correctif parasite).

| Contrôle | Résultat |
|---|---|
| Diff amend | 6 chemins uniquement ; `git diff --ignore-space-at-eol 5d4b3d0..e2a68f1` **vide** |
| Staged | uniquement les six md (force-add) ; aucun rapport/log validation |
| Message | inchangé : *Document Epic 5 and 6 completion and retrospectives.* |
| Stashs | les deux conservés |

### Pourquoi U5 / builds complets non rejoués

Preuve que **seul** `_bmad-output/…` (clôture) a changé entre `5d4b3d0` et `e2a68f1`, et que ces chemins sont **exclus des artefacts de distribution** :

- `MANIFEST.in` : `prune _bmad-output` + `recursive-exclude _bmad-output *`
- `scripts/verify_artifacts.py` liste `_bmad-output` hors payload utile wheel/sdist
- Aucun fichier sous `src/`, `tests/`, `docs/`, `pyproject.toml`, `_version.py`, `CHANGELOG.md` dans le delta amend

Donc le wheel/sdist, l’install hors checkout et les 24 parcours U5 déjà prouvés sur le contenu runtime de `5d4b3d0` restent valides pour `e2a68f1`. Rejeu limité : whitespace + suite.

### Rejeu post-amend (attaché à `e2a68f1`)

| Contrôle | Exit | Log |
|---|---:|---|
| `check_release_whitespace.py` | 0 | `logs/whitespace-recheck.exit` |
| `tests/test_release_whitespace.py` | 0 | `logs/whitespace-test-recheck.exit` (5 passed) |
| Suite complète | 0 | `logs/full-suite-recheck.exit` — **1254 passed**, 16 skipped |

## Verdict global

**PASS** après amend whitespace.

(État initial de la campagne : CONDITIONAL FAIL sur trailing whitespace des 6 md de clôture dans `5d4b3d0` ; corrigé par amend.)

## Contrôles techniques

| Contrôle | Exit | Détail |
|---|---:|---|
| Whitespace (`check_release_whitespace.py`) | 0 | recheck sur `e2a68f1` (était 2 sur `5d4b3d0`) |
| Tests Epic 3 (doctor/runtime) | 0 | 33 passed, 3 skipped |
| Tests Epic 4 (ingest) | 0 | 30 passed, 1 skipped |
| Tests Epic 5 (declare) | 0 | 26 passed, 1 skipped |
| Tests Epic 6 (guidance/claude) | 0 | 45 passed, 1 skipped |
| Suite complète | 0 | **1254 passed**, 16 skipped (recheck `e2a68f1`) |
| Double build reproductible (`SOURCE_DATE_EPOCH` = HEAD) | 0 | Contrat Release verrouillé `--no-isolation` |
| Twine check | 0 | wheel + sdist PASSED |
| check-wheel-contents | 0 | OK |
| verify_artifacts.py | 0 | modules E3–E6 + schémas présents |
| Wheel reconstruit depuis sdist | 0 | SHA256 **identique** au wheel direct |
| Install wheel venv neuf hors checkout | 0 | `…/Temp/aef-epics-3-6-validation-…/venv-u5` |
| CLI / module | — | `aef 1.2.0` / `1.2.0` ; path sous Temp (hors repo) |
| `_version.py` / CHANGELOG / pyproject | — | **inchangés**, restent **1.2.0** |

### Hashes artefacts

- Wheel : `71425B8289469B1F66C5FD3E00293A5665F493BF8BCE4EB8BCFB873859E66F6F`
- Sdist : `8F6DF86B8EC8683857E1AFED1891432F1C207B3E1D2F8CBA11EECEE66BC0F143`
- Wheel-from-sdist : **match** avec wheel direct

## Parcours utilisateurs (hors dépôt)

Racine temporaire : `%TEMP%/aef-epics-3-6-validation-…/journeys`
CLI : venv U5 installé depuis le wheel local
Résumé : `user-journeys-summary.json` — **24/24 OK**

### Epic 3 — Doctor / runtime

| Scénario | OK | Exit / note |
|---|---|---|
| Runtime présent | oui | 0 / `decision=OK`, pas de mutation |
| INSTALL_REQUIRED | oui | 8 (hook discovery via paquet installé) |
| Refus de consentement | oui | `InstallRefused` |
| Env incompatible préservé | oui | `venv_status=incompatible` |
| Install isolée consentie + wheel local | oui | `.aef-venv` créé ; deps pip cache sous consentement explicite |
| Symlink hors workspace | skip OS | symlink non disponible / traité |

### Epic 4 — INGEST

| Scénario | OK | Exit |
|---|---|---:|
| Dry-run | oui | 0 |
| Premier apply | oui | 0 / CHANGE |
| Replay | oui | 0 / NO_CHANGE |
| Conflit digest | oui | 4 / BLOCKED\|ERROR |
| AUDIT | oui | 0 |

### Epic 5 — Déclaration

| Scénario | OK | Exit |
|---|---|---:|
| Dry-run | oui | 0 |
| Apply / décision humaine | oui | 0 / CHANGE ; pas d’XP/niveau/permission inventés |
| Replay | oui | 0 / NO_CHANGE |
| Conflit (casefold id) | oui | 4 |
| Récupération journal | oui | 0 / rollback |
| AUDIT | oui | 0 |

### Epic 6 — Guidance

| Scénario | OK | Exit |
|---|---|---:|
| Dry-run | oui | 0 |
| Install bridge (all) | oui | 0 / CHANGE |
| Replay | oui | 0 / NO_CHANGE |
| Contenu utilisateur préservé | oui | 0 |
| Conflit de marqueurs / drift | oui | 4 / BLOCKED |
| `--status` | oui | 0 |
| AUDIT | oui | 0 (santé guidance = `--status`) |

Sentinelles extérieures : intactes sur tous les parcours OK. Mutations uniquement dans projets temporaires.

## Commits d’avance (rappel)

1. `ee00e27` Add Runtime Bootstrap and Doctor.
2. `f57a1b3` Add governed INGEST transition.
3. `c4a08ef` Add governed competency declaration.
4. `c2f467a` Add governed agent guidance integration.
5. `85e28a2` Update installation references to AEF 1.2.0.
6. `e2a68f1` Document Epic 5 and 6 completion and retrospectives. *(amend de `5d4b3d0`, trailing whitespace)*

## Suite proposée (humain)

1. Examiner le dossier `validation-epics-3-6-2026-08-21/`.
2. Commit de preuves **séparé** (pas dans l’histoire « produit » au-delà du nécessaire).
3. **Alors seulement** : PR brouillon (sans bump, sans tag, sans Release).
