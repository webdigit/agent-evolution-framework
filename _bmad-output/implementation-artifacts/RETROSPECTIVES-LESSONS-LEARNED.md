# Lessons learned — AEF (local)

## 2026-08-21 — Epic 6 implémentation (clôture)

- Variante B : **une** copie de règles = `AGENTS.md` ; sonnettes = redirection sans règle.
- `integrate claude` conserve le nom public mais pose la sonnette **racine** ; `.claude/` = brownfield status/remove seulement.
- Marqueurs distincts (`AEF:AGENTS` / `AEF:CLAUDE-ROOT` / `AEF:GEMINI` / legacy `AEF:CLAUDE-PROJECT`) ; préfixe BEGIN sans `"` d’ouverture de version.
- Santé guidance = `--status` ; ne pas inventer de findings `audit` hors `.agent/` (Q10).
- UPGRADE / `init` : `BOOTSTRAP_NAMES` inclut `GEMINI.md` ; jamais de création silencieuse.
- Checkout AEF : interdiction de committer des sonnettes — tests d’isolation.

## 2026-08-21 — Epic 5 implémentation (clôture)

- Naissance ≠ promotion : `competency declare` écrit L1 ; EVALUATE ne crée pas.
- RECORD cite ; l’humain décide ; AEF n’infère pas la compétence depuis un outcome.
- Journal crash **dédié** + ledger ; garde mutuelle avec evaluate / upgrade / ingest.
- Brownfield : warning `competency-missing-declaration-provenance` non bloquant ; pas de fiction.
- Collision casefold/NFC → `BLOCKED` sans normalisation (Q9).
- `epic-completion-yolo` : ne pas réécrire `docs/` déjà livrés en 5.6 ; brouillon recovery sous `_bmad-output/`.

## 2026-08-21 — Epic 4 implémentation (clôture)

- RECORD cite un fait ; l’intake **déclare** l’événement ; `ingest_events` dérive — ne jamais élargir `aef record` avec `--learn`.
- Q8 sans ledger : coller la provenance sur les items knowledge (`source_records`) évite une migration UPGRADE fictive.
- Un second intake sur le même signal doit **fusionner** la provenance, pas l’écraser.
- AUDIT : absence d’ingest n’est pas un finding ; findings provenance ≠ findings RECORD.
- Ingest ≠ doctor : documenter explicitement pour éviter qu’un agent confonde BLOCKED citation et `INSTALL_REQUIRED`.
- `epic-completion-yolo` : ne pas réécrire `docs/` déjà livrés en 4.6 ; brouillon recovery optionnel seulement sous `_bmad-output/`.

## 2026-08-20 — U5 hors checkout

- Valider le wheel installé hors checkout, pas le worktree : un checkout accidentel sur `main` produit un wheel sans modules UPGRADE.
- Sur cible productive `1.0.0`, U5 doit rester `NO_CHANGE` ; le apply mutant appartient aux tests synthétiques.

## 2026-08-20 — Epic 2 implémentation (clôture)

- Distinguer Update et Upgrade **avant** le code évite un lot réseau ; le tenir dans la CLI (`pas de --target-schema`) est le vrai test.
- Ne pas wrapper `upgrade_project` / `apply_framework_release` : extraire le planificateur, nouveau robinet.
- Un `transaction_id` ne peut pas hasher le ledger qui le contient — calculer l’identité hors bytes ledger.
- Finding AUDIT : suivre la convention existante à tirets (`upgrade-recovery-required`), pas l’underscore de la prose SPEC.
- `epic-completion-yolo` Siftbox (push `staging`) ne s’applique pas tel quel : AEF skip git sauf demande ; `docs/` seulement après le code (ici : story 2.6).

## 2026-08-20 — Epic 2 cadrage (rétro partielle)

- Distinguer Update et Upgrade **avant** le code évite un lot réseau.
- Ne pas inventer une migration de production pour démontrer un moteur.
- `upgrade_project` 1.1.2 est un lab, pas un robinet.
- Une commande Siftbox `epic-completion-yolo` ne se copie pas telle quelle tant que l’Epic n’est pas `done`.
