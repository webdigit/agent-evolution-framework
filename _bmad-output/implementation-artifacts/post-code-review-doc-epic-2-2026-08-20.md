# Post-documentation Epic 2 — 2026-08-20

**Niveau :** épique (implémentation livrée).  
**Version documentée (baseline, pas bump) :** AEF **1.1.2**.

## Adaptation AEF vs Siftbox

| Livrable Siftbox | Décision AEF cette passe |
|---|---|
| `docs/` technique | **Déjà écrit en 2.6** — `docs/commands.md` § UPGRADE, `docs/recovery.md` § UPGRADE. Cette commande ne les réécrit pas. |
| `docs/chatbot/` | N/A |
| Marketing / features.md | N/A — CLI locale |
| `CHANGELOG.md` | **Non touché** — hors lot (workflow Release) |
| `docs/user-releases/` | N/A |
| Commit + push `staging` | **SKIP** |

## Story → docs

| Story | Doc suivie | Statut |
|---|---|---|
| 2.3–2.5 | `docs/commands.md` — section UPGRADE | **fait** (2.6) |
| 2.5 | `docs/recovery.md` — journal UPGRADE en plus d’EVALUATE | **fait** (2.6) |
| 2.6 | isolation + cinq formes | **fait** |
| 2.1–2.2 | pas de doc publique | plan 2.1 sous `_bmad-output/` |

Brouillons historiques : `epic-2-doc-draft-commands-upgrade.md`, `epic-2-doc-draft-recovery-upgrade.md` (fusionnés, conservés pour trace).

## Checklist post-doc

- [x] Brouillons UPGRADE
- [x] `docs/commands.md` — cinq formes, pas d’Update, pas de `--target-schema`
- [x] `docs/recovery.md` — journal UPGRADE distinct ; section EVALUATE conservée
- [ ] CHANGELOG — **hors lot** (Release)
- [x] Delta privacy : N/A — local au workspace, aucun réseau, aucun sous-traitant

## Delta privacy / conformité

1. Nouveau traitement de données ? **Non**.
2. Nouveau sous-traitant / flux ? **Non**.
3. Impact politique / DPA / DPIA ? **N/A justifié** — CLI air-gap.

## Git

**SKIP** — pas de commit, pas de push, pas de `staging`.
