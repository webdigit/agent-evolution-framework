# Post-documentation Epic 6 — 2026-08-21

**Niveau :** épique (implémentation livrée).
**Version documentée (baseline, pas bump) :** AEF **1.2.0**.

## Adaptation AEF vs Siftbox

| Livrable Siftbox | Décision AEF cette passe |
|---|---|
| `docs/` technique | **Déjà écrit en 6.6** — `commands.md` § Guidance, `claude-integration.md`, `getting-started.md`, `README.md`. Cette commande ne les réécrit pas. |
| `docs/chatbot/` | N/A |
| Marketing | N/A |
| `CHANGELOG.md` | **Non touché** |
| Commit + push `staging` | **SKIP** |

## Story → docs

| Story | Doc suivie | Statut |
|---|---|---|
| 6.3–6.4 | `docs/commands.md` — integrate agents/claude/gemini/all | **fait** (6.6) |
| 6.5 | brownfield `.claude/` + UPGRADE ne crée pas | **fait** (docs + tests) |
| 6.6 | isolation ; pas de sonnettes dans le repo AEF | **fait** |
| 6.1 | pas de doc publique | plan sous `_bmad-output/` |
| recovery | pas de journal guidance | brouillon : `epic-6-doc-draft-recovery-guidance.md` |

## Checklist post-doc

- [x] Brouillon recovery guidance (pas de `--recover`)
- [x] `docs/commands.md` / `claude-integration.md` / README / getting-started
- [x] Mémoire Claude / Auto Memory boundaries conservées dans `claude-integration.md`
- [ ] `docs/recovery.md` — mention guidance **non fusionnée**
- [ ] CHANGELOG — **hors lot**
- [x] Delta privacy : N/A — guidance projet-local, pas de settings hôte

## Delta privacy / conformité

1. Nouveau traitement ? **Non**.
2. Sous-traitant / flux ? **Non** (pas de réseau, pas de mémoire de compte).
3. DPIA ? **N/A**.

## Git

**SKIP** — pas de commit, pas de push, pas de `staging`.
