#!/usr/bin/env python3
"""Active (ou desactive) la consultation `/gk` dans le config.yaml de l'agent.

Une seule insertion, sous `plugins.entries.gk-bridge` :

    env:
      GK_CONSULTATION: "1"

C'est la variable que le pont lit a l'import pour autoriser la seule voie par
laquelle il rend autre chose que `None`. Sans elle, un message commencant par
`/gk` est observe comme n'importe quelle demande et rien n'est injecte.

Pourquoi un script plutot qu'une edition a la main : le fichier fait plusieurs
centaines de lignes, il est en production, et une erreur d'indentation YAML ne
se voit qu'au redemarrage de l'agent -- c'est-a-dire quand les collegues ne
peuvent plus travailler.

Pourquoi une insertion ciblee plutot qu'une relecture YAML : reecrire le fichier
via un analyseur detruirait commentaires, ordre et mise en forme. On insere donc
apres des ancres exactes, et on ne touche a rien d'autre.

Garanties, les memes que `brancher-gk.py` :

* **sauvegarde horodatee** avant toute ecriture ;
* **idempotent** : relancer ne duplique rien ;
* **validation** apres ecriture, et **restauration** de la sauvegarde si le YAML
  ne se relit pas ou si la valeur n'est pas la ou on l'attend ;
* ecriture **en place**, donc le fichier garde son proprietaire et ses droits --
  c'est ce qui avait casse l'agent le 2026-10-22, un fichier recree en root ;
* **refus plutot que degats** : si la forme rencontree n'est pas celle attendue
  (un `env:` en ligne, une entree `gk-bridge` absente), le script s'arrete sans
  rien ecrire.

Usage, depuis l'hote :

    docker compose -f ~/next_hermes/compose.yaml exec -u 1001 -T hermes \\
        python3 - < ~/greenknowledge/deploy/activer-consultation.py

Pour desactiver :

    docker compose -f ~/next_hermes/compose.yaml exec -u 1001 -T hermes \\
        python3 - --desactiver < ~/greenknowledge/deploy/activer-consultation.py

Un redemarrage de l'agent est necessaire dans les deux cas : la variable est
lue a l'import du plugin.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

CONFIG = Path("/opt/data/config.yaml")
VARIABLE = "GK_CONSULTATION"
VALEUR = "1"

# L'indentation reelle du fichier, relevee : `plugins:` a la racine, `entries:`
# a deux espaces, `gk-bridge:` a quatre, ses clefs a six.
IND_ENTREE = 4
IND_CLEF = 6
IND_VARIABLE = 8


def trouver(lignes: list[str], texte: str, depuis: int = 0) -> int:
    """Index de la premiere ligne egale a *texte*, bords ignores."""
    for i in range(depuis, len(lignes)):
        if lignes[i].rstrip() == texte:
            return i
    return -1


def indentation(ligne: str) -> int:
    return len(ligne) - len(ligne.lstrip(" "))


def fin_de_bloc(lignes: list[str], debut: int, ind_parent: int) -> int:
    """Index juste apres la derniere ligne du bloc ouvert a *debut*.

    Le bloc s'arrete a la premiere ligne non vide dont l'indentation ne depasse
    pas celle de son parent. Les lignes vides et les commentaires alignes plus a
    droite restent dedans, pour ne pas inserer au milieu d'un commentaire.
    """
    i = debut + 1
    dernier = debut
    while i < len(lignes):
        ligne = lignes[i]
        if ligne.strip():
            if indentation(ligne) <= ind_parent:
                break
            dernier = i
        i += 1
    return dernier + 1


def main(argv: list[str]) -> int:
    desactiver = "--desactiver" in argv

    if not CONFIG.is_file():
        print(f"ECHEC: {CONFIG} introuvable", file=sys.stderr)
        return 1

    original = CONFIG.read_text(encoding="utf-8")
    lignes = original.splitlines()

    # -- reperage des ancres, avant toute modification ---------------------
    i_plugins = trouver(lignes, "plugins:")
    if i_plugins < 0:
        print("ECHEC: aucune section 'plugins:' a la racine", file=sys.stderr)
        return 1
    i_entries = trouver(lignes, "  entries:", i_plugins)
    if i_entries < 0:
        print("ECHEC: 'entries:' absent sous plugins:", file=sys.stderr)
        print("       lancer d'abord brancher-gk.py", file=sys.stderr)
        return 1
    i_entree = trouver(lignes, "    gk-bridge:", i_entries)
    if i_entree < 0:
        print("ECHEC: 'gk-bridge:' absent de plugins.entries", file=sys.stderr)
        print("       lancer d'abord brancher-gk.py", file=sys.stderr)
        return 1

    fin_entree = fin_de_bloc(lignes, i_entree, IND_ENTREE)

    # -- la variable est-elle deja la, et sous quel `env:` ? ---------------
    i_env = -1
    i_variable = -1
    for i in range(i_entree + 1, fin_entree):
        nu = lignes[i].strip()
        if indentation(lignes[i]) == IND_CLEF and nu.split(":")[0] == "env":
            # Un `env: {...}` en ligne ne se complete pas par insertion.
            if nu != "env:":
                print(
                    f"ECHEC: `env:` est ecrit en ligne ({nu!r})", file=sys.stderr
                )
                print(
                    "       a completer a la main, ce script ne sait pas le "
                    "faire sans risque", file=sys.stderr,
                )
                return 1
            i_env = i
        if nu.split(":")[0] == VARIABLE:
            i_variable = i

    if desactiver:
        if i_variable < 0:
            print(f"RIEN A FAIRE: {VARIABLE} n'est pas dans le fichier")
            return 0
    else:
        if i_variable >= 0:
            valeur = lignes[i_variable].split(":", 1)[1].strip().strip('"\'')
            if valeur == VALEUR:
                print(f"RIEN A FAIRE: {VARIABLE} est deja a {VALEUR!r}")
                return 0
            print(
                f"ATTENTION: {VARIABLE} vaut {valeur!r}, il sera mis a "
                f"{VALEUR!r}"
            )

    # -- sauvegarde ---------------------------------------------------------
    horodatage = time.strftime("%Y%m%d-%H%M%S")
    sauvegarde = CONFIG.with_name(f"{CONFIG.name}.bak-avant-consultation-{horodatage}")
    shutil.copy2(CONFIG, sauvegarde)
    print(f"sauvegarde: {sauvegarde}")

    # -- la modification ----------------------------------------------------
    if desactiver:
        del lignes[i_variable]
        fait = f"retire: {VARIABLE}"
    elif i_variable >= 0:
        lignes[i_variable] = f'{" " * IND_VARIABLE}{VARIABLE}: "{VALEUR}"'
        fait = f"corrige: {VARIABLE} -> {VALEUR!r}"
    elif i_env >= 0:
        lignes.insert(i_env + 1, f'{" " * IND_VARIABLE}{VARIABLE}: "{VALEUR}"')
        fait = f"insere sous le `env:` existant: {VARIABLE}"
    else:
        lignes[fin_entree:fin_entree] = [
            f'{" " * IND_CLEF}env:',
            f'{" " * IND_VARIABLE}{VARIABLE}: "{VALEUR}"',
        ]
        fait = f"insere avec un nouveau `env:`: {VARIABLE}"

    CONFIG.write_text("\n".join(lignes) + "\n", encoding="utf-8")

    # -- validation, et restauration si quoi que ce soit cloche ------------
    def restaurer(motif: str) -> int:
        shutil.copy2(sauvegarde, CONFIG)
        print(f"ECHEC: {motif}", file=sys.stderr)
        print("RESTAURE: le fichier est revenu a son etat initial", file=sys.stderr)
        return 1

    try:
        import yaml
    except ImportError:
        print("ATTENTION: PyYAML absent, validation impossible")
        print("           verifie manuellement AVANT de redemarrer l'agent :")
        print("           sed -n '/gk-bridge:/,/^    [a-z]/p' /opt/data/config.yaml")
        print(fait)
        return 0

    try:
        charge = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        return restaurer(f"YAML invalide apres modification ({exc!r})")

    entree = (
        ((charge or {}).get("plugins") or {}).get("entries") or {}
    ).get("gk-bridge")
    if not isinstance(entree, dict):
        return restaurer("plugins.entries.gk-bridge introuvable apres relecture")

    # Le reste de l'entree ne doit pas avoir bouge : c'est elle qui autorise
    # `call_mcp`, et la perdre couperait le pont en silence.
    if "gk" not in (entree.get("mcp_allowlist") or []):
        return restaurer("mcp_allowlist ne contient plus 'gk'")

    env = entree.get("env") or {}
    present = str(env.get(VARIABLE)) if VARIABLE in env else None
    if desactiver:
        if present is not None:
            return restaurer(f"{VARIABLE} est encore la apres retrait")
    else:
        if present != VALEUR:
            return restaurer(
                f"{VARIABLE} vaut {present!r} apres relecture, attendu {VALEUR!r}"
            )

    print(fait)
    print("VALIDE: le YAML se relit, et l'entree du plugin est intacte")
    print()
    if desactiver:
        print("La consultation `/gk` sera inerte au prochain redemarrage.")
    else:
        print("Prochaine etape : REDEMARRER l'agent -- la variable est lue a")
        print("l'import du plugin, donc elle ne prend pas effet a chaud.")
        print()
        print("Avant de redemarrer, verifier les proprietaires (c'est ce qui a")
        print("casse l'agent le 2026-10-22) :")
        print("    ls -ln /opt/data/*.json /opt/data/config.yaml")
        print("Tout doit etre en 1001 1001.")
        print()
        print("Apres redemarrage, essayer depuis Discord :")
        print("    /gk pourquoi on nous facture le port chez Adagio")
        print("et lire /opt/data/gk-bridge.jsonl : attendu")
        print('    {"event": "consultation", "rendu": true}')
        print("Si la ligne dit `consultation_desactivee`, Hermes ne propage pas")
        print("`env` par entree de plugin : poser la variable sur le service")
        print("`hermes` du compose a la place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
