# Delivery pipeline — Epic 2 — 2026-08-20

- **Commande :** `/bmadbmmworkflowsdelivery-story-pipeline EPIC 2 mode YOLO` + « jusqu’au bout du dev »
- **Mode :** YOLO
- **Portée :** Epic 2 (opp-2 GO)
- **Workflow :** Siftbox 1a–1g importé (agnostique) sous `.cursor/rules/bmad/bmm/workflows/`

## Périmètre exécuté

| Étape | Statut |
|---|---|
| 1a create-story | skip — fichiers déjà présents |
| 1b–1c validate + risques | OK |
| 1d dev-story 2.1–2.6 | OK |
| 1e Sally UI | skip — pas d’impact UI |
| 1f test-story | OK — suite **1090 passed**, 10 skipped (HEAD `bfd79f6`) |
| 1g code-review | OK — cycle `transaction_id`/ledger corrigé ; `main()` BLOCKED sans `error` |
| Delivery Loop privé | **non lancé** |
| Version / tag / Release | **non bumpés** |

## Stories

| Clé | Chaîne | Statut |
|---|---|---|
| `epic-2-1-audit-brownfield-plan-fichiers` | create skip / validate / dev (plan) / Sally skip / review | done |
| `epic-2-2-contrats-upgrade-v1` | … / dev contrats / tests hors I/O | done |
| `epic-2-3-cli-check-dry-run` | … / CLI check+dry-run | done |
| `epic-2-4-appliquer-transaction` | … / apply + journal | done |
| `epic-2-5-recuperer-auditer` | … / recover + AUDIT + exclusion | done |
| `epic-2-6-cloisonnement-documentation` | … / isolation + docs | done |

## Dépendances

`2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6` — respecté. Lab `upgrade_project` / `apply_framework_release` non exposés.

## Validation locale + U5

Porte exécutée sur `feature/upgrade-v1-x` : tests ciblés (51) + suite 1090 passed ; wheel/sdist + twine / check-wheel-contents / `verify_artifacts.py` ; U5 hors checkout, cible `1.0.0`, cinq formes `NO_CHANGE`. Preuves : `dev-final-upgrade-v1-x-2026-08-20.md`, `u5-upgrade-2026-08-20.md`. **Pas de push.** Stash et `release/prepare-1.1.2` conservés.

## Clôture documentaire

Epic livrée (`done`). Rétro déjà écrite. Publication : uniquement après PR distante dont le HEAD égale le candidat local.

---

*Pas de Delivery Loop privé. Pas de bump 1.1.2.*
