# Post-documentation Epic 5 — 2026-08-21

**Niveau :** épique (implémentation livrée).
**Version documentée (baseline, pas bump) :** AEF **1.2.0**.

## Adaptation AEF vs Siftbox

| Livrable Siftbox | Décision AEF cette passe |
|---|---|
| `docs/` technique | **Déjà écrit en 5.6** — `commands.md` § COMPETENCY DECLARE, `input-files.md`, exemple `competency-declaration.json`, `concepts.md`, `getting-started.md`. Cette commande ne les réécrit pas. |
| `docs/chatbot/` | N/A |
| Marketing | N/A |
| `CHANGELOG.md` | **Non touché** — hors lot |
| Commit + push `staging` | **SKIP** |

## Story → docs

| Story | Doc suivie | Statut |
|---|---|---|
| 5.3–5.4 | `docs/commands.md` — COMPETENCY DECLARE + recover | **fait** (5.6) |
| 5.2 | `docs/input-files.md` + exemple | **fait** (5.6) |
| 5.5 | AUDIT brownfield dans `commands.md` § AUDIT | **fait** (5.6) |
| 5.6 | isolation + parcours | **fait** |
| 5.1 | pas de doc publique | plan sous `_bmad-output/` |
| recovery | journal déclaration distinct | brouillon : `epic-5-doc-draft-recovery-declare.md` |

## Checklist post-doc

- [x] Brouillon recovery déclaration
- [x] `docs/commands.md` — declare ≠ evaluate / ingest
- [x] `docs/input-files.md` + exemple exécutable
- [x] `docs/concepts.md` — naissance L1 gouvernée
- [ ] `docs/recovery.md` — section déclaration **non fusionnée** (brouillon seulement)
- [ ] CHANGELOG — **hors lot**
- [x] Delta privacy : N/A — local workspace, aucun réseau

## Delta privacy / conformité

1. Nouveau traitement ? **Non** (état compétences déjà projet-local).
2. Sous-traitant / flux ? **Non**.
3. DPIA ? **N/A** — CLI air-gap.

## Git

**SKIP** — pas de commit, pas de push, pas de `staging`.
