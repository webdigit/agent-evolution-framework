# Rapport DEV final — UPGRADE V1.x — 2026-08-20

- **Branche :** `feature/upgrade-v1-x`
- **Candidat code :** `bfd79f627f7705267faf489f997d9ab7fa9b8414`
- **Baseline publique :** AEF **1.1.2** (non bumpée, pas de tag, pas de Release)
- **Porte :** validation locale + U5 hors checkout. **Aucun push.**

## Verdict

**Accepté.** Les tests ciblés UPGRADE, la suite complète, le contrat Release et U5 (cible productive `1.0.0`) sont verts. Aucune migration publique n’a été injectée. Le scénario mutant `1.0.0→1.1.0→1.2.0` reste uniquement dans les tests synthétiques.

## Tests (HEAD `bfd79f6`)

| Lot | Résultat |
|---|---|
| Ciblé UPGRADE (`test_upgrade_plan`, `test_cli_upgrade`, `test_upgrade_isolation`, `test_cli_protocol`) | **51 passed** |
| Suite complète | **1090 passed**, 10 skipped, 1 warning (zipfile duplicate-member inspector) |

## Artefacts Release (contrat verrouillé)

Build déjà produit depuis `feature/upgrade-v1-x` via `scripts/reproducible_build.py` + `requirements-release.txt` (setuptools 84.0.0). Contrôles relancés sur les mêmes fichiers :

| Fichier | SHA-256 | Taille |
|---|---|---|
| `dist/agent_evolution_framework-1.1.2-py3-none-any.whl` | `d94e73085a3518c69ef42d6440306c3dec6ac04377b02634bfe18bcd42e81bc1` | 101922 |
| `dist/agent_evolution_framework-1.1.2.tar.gz` | `94e2e2f287af0f388a2b2f134744816bb2dd295a1e98496d0ebe0da69dc79c96` | 178932 |

| Contrôle | Sortie |
|---|---|
| `twine check` | PASSED / exit 0 (wheel + sdist) |
| `check-wheel-contents` | OK / exit 0 |
| `scripts/verify_artifacts.py` | OK / exit 0 ; schéma `upgrade-transaction.schema.json` présent ; sdist sans `_bmad-output` |

Le wheel contient `upgrade_compat.py`, `upgrade_plan.py`, `upgrade_ops.py`, `upgrade_transaction.py` et le schéma journal.

## U5 — projet temporaire initialisé par ce wheel

Preuve détaillée : [u5-upgrade-2026-08-20.md](u5-upgrade-2026-08-20.md).

- Venv : `%TEMP%\aef-u5-20260820\venv` (hors checkout, `PYTHONPATH` retiré)
- Projet : `%TEMP%\aef-u5-20260820\project` après `aef init` + `aef record`
- Cible : `schema_version` `1.0.0` ; pas de `--target-schema`
- Versions : `aef --version` = `python -m aef --version` = `aef 1.1.2`

| Commande | exit | statut | dry_run | journal | empreinte |
|---|---|---|---|---|---|
| `aef upgrade --check` | 0 | `NO_CHANGE` | true | absent | inchangée |
| `aef upgrade --dry-run` | 0 | `NO_CHANGE` | true | absent | inchangée |
| `aef upgrade` | 0 | `NO_CHANGE` | false | absent | inchangée |
| `aef upgrade --recover --dry-run` | 0 | `NO_CHANGE` (`reason=no_upgrade_transaction`) | true | non créé | inchangée |
| `aef upgrade --recover` | 0 | `NO_CHANGE` (`reason=no_upgrade_transaction`) | false | non créé | inchangée |
| `aef audit` | 0 | `PASS`, findings `[]` | — | absent | inchangée |

Empreinte arbre stable : `a42b39d565582d23b1a16271341073f51e4801fca25a72c26581fbf3850595d6` (17 fichiers). RECORD, compétences, connaissances et bootstrap (`AGENTS.md` / `CLAUDE.md`) identiques octet à octet. Sentinelles extérieures et jetons d’environnement absents des sorties.

## Hygiene git

- Branche de travail : `feature/upgrade-v1-x` uniquement.
- `stash@{0}` `upgrade-v1-x-wip-before-rebranch` **conservé**.
- `release/prepare-1.1.2` (`510f125`, ahead 1 vs `origin`) **conservée**, non poussée.
- `dist/` non commité.
- Push / suppression des parachutes : **après** PR distante dont le HEAD égale le candidat local.

## Hors lot

Delivery Loop privé, bump 1.1.2, tag, Release, CHANGELOG de publication.
