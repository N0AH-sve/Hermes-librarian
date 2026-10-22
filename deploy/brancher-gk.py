#!/usr/bin/env python3
"""Branche GreenKnowledge dans le config.yaml de l'agent.

Trois insertions dans `/opt/data/config.yaml` :

* `mcp_servers.gk`        — le serveur, sur la boucle locale ;
* `plugins.enabled`       — sans quoi le plugin n'est jamais charge ;
* `plugins.entries.gk-bridge` — dont `mcp_allowlist`, sans quoi `call_mcp`
                            leve une `PermissionError` (defaut-refus).

Pourquoi un script plutot qu'une edition a la main : le fichier fait plusieurs
centaines de lignes, il est en production, et une erreur d'indentation YAML ne
se voit qu'au redemarrage de l'agent.

Pourquoi une insertion ciblee plutot qu'une relecture YAML : reecrire le
fichier via un analyseur detruirait commentaires, ordre et mise en forme. On
insere donc apres des ancres exactes, et on ne touche a rien d'autre.

Garanties :

* **sauvegarde horodatee** avant toute ecriture, selon la convention du volume ;
* **idempotent** : relancer ne duplique rien ;
* **validation** apres ecriture, et **restauration** de la sauvegarde si le YAML
  ne se relit pas ;
* l'ecriture se fait en place, donc le fichier garde son proprietaire et ses
  droits ;
* le jeton n'apparait jamais : seule la reference `${GK_BRIDGE_TOKEN}` est
  ecrite.

Usage, depuis l'hote :

    docker compose -f ~/next_hermes/compose.yaml exec -T hermes \\
        python3 - < ~/greenknowledge/deploy/brancher-gk.py
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

CONFIG = Path("/opt/data/config.yaml")
URL = "http://127.0.0.1:8765/mcp/bridge"

BLOC_SERVEUR = [
    "  gk:",
    f"    url: {URL}",
    "    headers:",
    "      Authorization: Bearer ${GK_BRIDGE_TOKEN}",
]
LIGNE_ENABLED = "    - gk-bridge"
BLOC_ENTRY = [
    "    gk-bridge:",
    "      allow_tool_override: false",
    '      mcp_allowlist: ["gk"]',
]


def trouver(lignes: list[str], texte: str, depuis: int = 0) -> int:
    """Index de la premiere ligne egale a *texte*, bords ignores."""
    for i in range(depuis, len(lignes)):
        if lignes[i].rstrip() == texte:
            return i
    return -1


def main() -> int:
    if not CONFIG.is_file():
        print(f"ECHEC: {CONFIG} introuvable", file=sys.stderr)
        return 1

    original = CONFIG.read_text(encoding="utf-8")
    lignes = original.splitlines()

    # -- reperage des ancres, avant toute modification ---------------------
    i_plugins = trouver(lignes, "plugins:")
    i_mcp = trouver(lignes, "mcp_servers:")
    if i_plugins < 0:
        print("ECHEC: aucune section 'plugins:' a la racine", file=sys.stderr)
        return 1
    if i_mcp < 0:
        print("ECHEC: aucune section 'mcp_servers:' a la racine", file=sys.stderr)
        return 1
    i_enabled = trouver(lignes, "  enabled:", i_plugins)
    i_entries = trouver(lignes, "  entries:", i_plugins)
    if i_enabled < 0 or i_entries < 0:
        print("ECHEC: 'enabled:' ou 'entries:' absent sous plugins:", file=sys.stderr)
        return 1

    # -- ce qui est deja en place ------------------------------------------
    deja_serveur = trouver(lignes, "  gk:", i_mcp) >= 0
    deja_enabled = trouver(lignes, LIGNE_ENABLED, i_plugins) >= 0
    deja_entry = trouver(lignes, "    gk-bridge:", i_entries) >= 0
    if deja_serveur and deja_enabled and deja_entry:
        print("RIEN A FAIRE: les trois insertions sont deja presentes")
        return 0

    # -- sauvegarde ---------------------------------------------------------
    horodatage = time.strftime("%Y%m%d-%H%M%S")
    sauvegarde = CONFIG.with_name(f"{CONFIG.name}.bak-avant-gk-{horodatage}")
    shutil.copy2(CONFIG, sauvegarde)
    print(f"sauvegarde: {sauvegarde}")

    # -- insertions, de la fin vers le debut pour ne pas decaler les index --
    faits = []
    ordre = sorted(
        [
            (i_entries, BLOC_ENTRY, "plugins.entries.gk-bridge", deja_entry),
            (i_enabled, [LIGNE_ENABLED], "plugins.enabled", deja_enabled),
            (i_mcp, BLOC_SERVEUR, "mcp_servers.gk", deja_serveur),
        ],
        reverse=True,
    )
    for index, bloc, nom, deja in ordre:
        if deja:
            print(f"deja present: {nom}")
            continue
        lignes[index + 1 : index + 1] = bloc
        faits.append(nom)

    CONFIG.write_text("\n".join(lignes) + "\n", encoding="utf-8")

    # -- validation, et restauration si le YAML ne se relit pas -------------
    try:
        import yaml
    except ImportError:
        print("ATTENTION: PyYAML absent, validation impossible")
        print("           verifie manuellement avant de redemarrer l'agent")
        for nom in faits:
            print(f"insere: {nom}")
        return 0

    try:
        charge = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:
        shutil.copy2(sauvegarde, CONFIG)
        print(f"ECHEC: YAML invalide apres insertion ({exc!r})", file=sys.stderr)
        print("RESTAURE: le fichier est revenu a son etat initial", file=sys.stderr)
        return 1

    # -- et verifier que les valeurs sont bien la ou on les attend ----------
    problemes = []
    serveurs = (charge or {}).get("mcp_servers") or {}
    if "gk" not in serveurs:
        problemes.append("mcp_servers.gk absent apres relecture")
    elif serveurs["gk"].get("url") != URL:
        problemes.append(f"mcp_servers.gk.url inattendu: {serveurs['gk'].get('url')!r}")
    plugins = (charge or {}).get("plugins") or {}
    if "gk-bridge" not in (plugins.get("enabled") or []):
        problemes.append("gk-bridge absent de plugins.enabled")
    if "gk-bridge" in (plugins.get("disabled") or []):
        problemes.append("gk-bridge est AUSSI dans plugins.disabled, a retirer")
    entree = (plugins.get("entries") or {}).get("gk-bridge") or {}
    if "gk" not in (entree.get("mcp_allowlist") or []):
        problemes.append("mcp_allowlist ne contient pas 'gk'")

    if problemes:
        shutil.copy2(sauvegarde, CONFIG)
        for p in problemes:
            print(f"ECHEC: {p}", file=sys.stderr)
        print("RESTAURE: le fichier est revenu a son etat initial", file=sys.stderr)
        return 1

    for nom in faits:
        print(f"insere: {nom}")
    print("VALIDE: le YAML se relit et les trois valeurs sont en place")
    print("Prochaine etape: copier le pont, puis redemarrer l'agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
