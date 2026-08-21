# Brouillon — recovery / déclaration de compétence (Epic 5)

**Destination éventuelle :** `docs/recovery.md` (après demande explicite de fusion).
**Statut :** brouillon `_bmad-output/` seulement.

---

## COMPETENCY DECLARE — journal distinct

La naissance d’une compétence utilise un journal de crash :

`.agent/state/competency-declaration-transaction.json`
protocole `aef.competency-declare-transaction/v1`

Il est **distinct** de `evaluation-transaction.json` et `upgrade-transaction.json`.

### Recover

```console
aef competency declare --recover --dry-run
aef competency declare --recover
aef --json competency declare --recover
```

| Situation | Comportement | Action |
|---|---|---|
| Document invalide / décision absente | `ERROR` exit 3 | Corriger `aef.competency.declare.submit/v1` |
| Record manquant / digest / collision | `BLOCKED` exit 4 | Persister le record ; ne pas normaliser l’ID |
| Journal déclaration présent | autres mutations bloquées | `--recover` (dry-run d’abord) |
| Journal EVALUATE / UPGRADE | `BLOCKED` avant apply | recover de la commande concernée |
| AUDIT `competency-missing-declaration-provenance` | warning, PASS possible | ne pas inventer de provenance ; déclarer officiellement si voulu |
| AUDIT `competency-declaration-recovery-required` | error / FAIL | `--recover` |

### Inspecter sans écrire

```console
aef audit
aef competency declare --declaration FILE --dry-run
```

Ne pas réutiliser `aef evaluate --recover` ni `aef upgrade --recover` pour une naissance.
