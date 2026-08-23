# Banc de tests adversariaux

Ces scripts attaquent AEF **par l'extérieur** : ils lancent la vraie CLI sur de vrais workspaces jetables, en concurrence, sous charge, et face à des contenus hostiles. Ils complètent la suite `pytest` — ils ne la remplacent pas.

La distinction compte. La suite vérifie que le code fait ce qu'on attend sur les chemins prévus. Ce banc vérifie que les **garanties tiennent** face à ce qui n'était pas prévu : un dépôt cloné contenant des liens sortants ou une archive piégée, huit processus concurrents, une interruption au milieu d'une écriture, un fichier en lecture seule, un marqueur cité dans un bloc de code.

pytest ne collecte pas ce répertoire (`norecursedirs = adversarial`).

## Usage

Multiplateforme, Windows et POSIX.

Worktree détaché (mesure d'un SHA) :

```powershell
python 00-setup.py <SHA>
$env:AEF_BUILD='C:\Temp\audit-<SHA>'; python lance-tout.py
```

```bash
python3 00-setup.py <SHA>
AEF_BUILD=/tmp/audit-<SHA> python3 lance-tout.py
```

Arbre courant (CI : le checkout **est** l'arbre) :

```bash
python3 00-setup.py --current
AEF_BUILD="$(git rev-parse --show-toplevel)" python3 lance-tout.py
```

`00-setup.py` crée le venv (`.venv\Scripts` ou `.venv/bin` selon l'OS), installe le paquet en éditable, et **s'arrête net si l'arbre importé n'est pas celui du répertoire mesuré**. Chaque script rejoue ce contrôle au démarrage, via `bancenv.verifier_arbre_importe()`.

Les scripts qui exigent POSIX — `mkfifo`, liens symboliques, `SIGKILL`, `strace` — s'annoncent **« IGNORE sur Windows »** (code 77) plutôt que de rendre un succès qui n'en est pas un. Sous Windows, `05`, `07` et `10` restent donc à la charge d'un runner Linux.

Chaque script se lance aussi seul, avec le python du venv mesuré.

Sur GitHub Actions, le workflow `adversarial.yml` tourne sur Linux, la nuit, à la demande, ou lorsqu'une PR porte le label `adversarial`. Il n'est pas branché sur chaque push.

## Ce que chaque script prouve

| Script | Propriété vérifiée |
|---|---|
| `01-concurrence-ingest.py` | aucune écriture perdue n'est rapportée comme un succès (8, 16 et 32 processus) |
| `02-concurrence-declare.py` | la même propriété sur le chemin transactionnel |
| `03-concurrence-record.py` | en contention, blocage explicite plutôt que file silencieuse |
| `04-plafond-evidences.py` | au plafond d'évidences, blocage explicite plutôt qu'un `NO_CHANGE` trompeur |
| `05-dryrun-vs-apply.py` *(POSIX)* | `--dry-run` rend le même verdict que l'apply, pour sept états du journal |
| `06-taux-erreur-fs.py` | une commande légitime concurrente ne rend pas d'erreur de système de fichiers |
| `07-crash-sigkill.py` *(POSIX)* | une interruption ordinaire ne rend jamais le workspace irrécupérable |
| `08-audit-scopage.py` | l'audit est scopé : workspace hérité et promotion légitime restent `PASS` |
| `09-collision-identifiants.py` | gardes mutuelles croisées ; collision d'identifiant casse × normalisation |
| `10-epic3-runtime.py` *(POSIX)* | aucun binaire du dépôt exécuté, aucun accès réseau, aucune amplification zip |
| `11-hygiene-git.py` | le verrou d'exécution ne se retrouve ni dans `git status` ni dans l'historique |
| `12-fence-marqueurs.py` | un marqueur situé dans une fence markdown n'est pas un marqueur |
| `13-guidance-integrite.py` | agrégat bloqué et atomique, mode de fichier préservé, pas d'écrasement en course |
| `decompte.py <avant> <après>` | décomposition exacte du delta de tests entre deux worktrees |

## Trois règles de méthode

Elles ne sont pas décoratives : chacune vient d'un cas où une mesure semblait concluante et ne l'était pas.

**Un contrôle positif avant toute conclusion.** Un scénario qui ne discrimine pas ne prouve rien. « Zéro attaque réussie » peut vouloir dire « le chemin attaqué n'a jamais été atteint ». Chaque script inclut donc un cas qui **doit** réussir, et un cas qui **doit** échouer.

**Provoquer l'état plutôt que de le simuler.** Un test qui fabrique un journal de transaction à la main ne voit pas le défaut de reprise ; un test qui remplace une taille déclarée par un mock ne voit pas l'amplification zip. Les deux peuvent rester verts pendant que le défaut est ouvert.

**Comparer à la version précédente avant d'attribuer un défaut.** Un comportement surprenant n'est pas forcément une régression : il faut vérifier qu'il n'existait pas déjà.

## Codes de sortie

Un script sort en `0` si et seulement si toutes les propriétés qu'il vérifie sont tenues.

Un script sort en `77` lorsqu'il **IGNORE** la mesure faute de support de la plateforme. `lance-tout.py` compte cet état comme ignoré, jamais comme un succès.

Tout autre code non nul signifie qu'au moins une propriété n'est pas tenue. `lance-tout.py` agrège, **nomme** les scripts en échec, et sort lui-même non nul.
