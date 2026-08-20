# Lessons learned — AEF (local)

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
