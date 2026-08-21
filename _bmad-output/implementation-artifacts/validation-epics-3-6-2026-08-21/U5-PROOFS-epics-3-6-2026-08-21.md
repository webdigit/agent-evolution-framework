# Preuves U5 — Epics 3–6 (hors checkout)

**HEAD courant :** `e2a68f1d8f30dcf8303273f17d2a13128f09c318`
**Campagne U5 exécutée sur :** `5d4b3d0dd4242f25a9603ad18df47a104d1beac5`
**Rattachement :** amend clôture = trailing whitespace **uniquement** dans 6 md `_bmad-output/` exclus de `MANIFEST.in` / artefacts → builds + U5 **non rejoués** (voir DEV report).
**Version :** 1.2.0 (non bumpée)
**Hors checkout :** `<TEMP>/aef-epics-3-6-validation`

## Chaîne artefacts

| Étape | Preuve | Résultat |
|---|---|---|
| Double build reproductible | `logs/repro-build.exit` | 0 |
| Twine | `logs/twine.exit` | 0 |
| check-wheel-contents | `logs/check-wheel-contents.exit` | 0 |
| verify_artifacts | `logs/verify-artifacts.exit` | 0 |
| Rebuild wheel depuis sdist | `logs/wheel-from-sdist-build.exit` + `logs/wheel-hash-compare.txt` | 0 / match |
| Venv U5 | `logs/venv-u5-install.exit` | 0 |
| Versions | `logs/version-cli.txt`, `logs/version-module.txt` | `aef 1.2.0` / module `1.2.0` hors repo |
| Parcours | `user-journeys.json`, `user-journeys-summary.json`, `logs/user-journeys.txt` | 24/24 OK |

## Empreintes

```
direct_wheel=71425B8289469B1F66C5FD3E00293A5665F493BF8BCE4EB8BCFB873859E66F6F
from_sdist=71425B8289469B1F66C5FD3E00293A5665F493BF8BCE4EB8BCFB873859E66F6F
match=True
```

## Install path (preuve hors checkout)

```
cli_path …/<TEMP>/aef-epics-3-6-validation/venv-u5/Lib/site-packages/aef/cli.py
OUTSIDE_CHECKOUT_OK
```

## Post-amend (whitespace)

Corrigé par amend `5d4b3d0` → `e2a68f1`. Rechecks verts : `logs/whitespace-recheck.*`, `logs/whitespace-test-recheck.*`, `logs/full-suite-recheck.*`.

## Non-faits (volontaires)

- Pas de push
- Pas de bump de version
- Pas de tag / Release
- Pas de PR (en attente revue dossier validation + commit de preuves séparé)
