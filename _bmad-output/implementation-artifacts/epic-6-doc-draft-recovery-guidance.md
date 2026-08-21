# Brouillon — recovery / guidance (Epic 6)

**Destination éventuelle :** `docs/recovery.md` (après demande explicite de fusion).
**Statut :** brouillon `_bmad-output/` seulement.

---

## INTEGRATE (guidance) — pas de journal de transaction

`aef integrate` pose des segments gérés dans `AGENTS.md` / `CLAUDE.md` / `GEMINI.md`
(et peut retirer un pont brownfield `.claude/CLAUDE.md`). Il **n’écrit pas**
de fichier `.agent/state/*-transaction.json` pour ces portes.

Il n’existe donc **pas** de `aef integrate … --recover`.

### Quand l’intégration est bloquée

| Situation | Comportement | Action |
|---|---|---|
| Scope ≠ project | `ERROR` exit 3 | Utiliser `--scope project` |
| Workspace / doctrine manquante | `BLOCKED` | `aef init` + doctrines présentes |
| Segment `modified` / `ambiguous` | `BLOCKED`, fichier intact | Corriger manuellement hors segment ou retirer le segment géré si possible |
| Journal EVALUATE présent | `BLOCKED` sur install/remove | `aef evaluate --recover` |
| Symlink / chemin hors workspace | `BLOCKED` / ERROR filesystem | Remplacer par un fichier régulier |

### Inspecter sans écrire

```console
aef integrate all --status
aef integrate claude --status
aef integrate agents --dry-run
aef audit
```

Santé des portes = **`--status`**, pas `aef audit` (Q10).
UPGRADE / `init` ne créent jamais `AGENTS.md` / `CLAUDE.md` / `GEMINI.md`.
