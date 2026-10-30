"""Lecture des fichiers de regles, et leur empreinte.

Trois fichiers de regles vivent cote a cote, tous rechargeables a chaud :
`motifs.yaml` (les aveux, lu par `patterns.py`), `optimiseur.yaml` (la
coherence des demandes) et `lexique.yaml` (le francais). Les deux derniers
passent par ici.

**Pas de PyYAML**, pour la meme raison que dans `config.py` et `patterns.py` :
une dependance de moins sur le chemin de service est une dependance de moins a
mettre a jour sur un serveur cense tourner sans surveillance. Le lecteur
ci-dessous ne couvre donc qu'un sous-ensemble de YAML — des sections de
premier niveau contenant soit une liste de chaines, soit un dictionnaire, soit
une liste de dictionnaires. C'est exactement ce que nos fichiers utilisent, et
une ligne qu'il ne comprend pas est ignoree plutot que fatale.

**L'empreinte est le point important.** La discipline de l'etape 4 s'applique
telle quelle : les regles sont gelees pendant une mesure, leur empreinte est
enregistree avec chaque arret, et `gk stats` refuse de comparer deux periodes
dont les empreintes diffèrent. Sans cela, ajouter une regle de contradiction au
milieu d'une mesure ferait monter le nombre d'arrets sans que rien n'ait change
dans les demandes de l'utilisateur.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Une cle de dictionnaire, et rien d'autre. Le discriminant est necessaire :
# sans lui, l'element de liste `- "(?:supprim|efface)"` se lirait comme un
# mapping de cle `"(?` — une expression reguliere contient des deux-points.
CLE = re.compile(r"^[a-z_][a-z0-9_]*$")

Section = dict[str, str] | list[str] | list[dict[str, str]]


def empreinte(*parties: str) -> str:
    """Empreinte courte et stable d'un jeu de regles."""
    h = hashlib.sha256()
    for partie in parties:
        h.update(partie.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _nettoyer(valeur: str) -> str:
    valeur = valeur.strip()
    if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
        # Entre guillemets, on ne touche a rien : une expression reguliere peut
        # contenir un diese, et le couper la casserait silencieusement.
        return valeur[1:-1]
    return valeur.split(" #")[0].strip()


def lire_sections(texte: str) -> dict[str, Section]:
    """Lit des sections de premier niveau. Les lignes incomprises sont ignorees."""
    out: dict[str, Section] = {}
    section: str | None = None
    courant: dict[str, str] | None = None  # mapping en cours dans une liste

    for ligne in texte.splitlines():
        nue = ligne.strip()
        if not nue or nue.startswith("#"):
            continue
        if not ligne[:1].isspace():
            section = nue[:-1].strip() if nue.endswith(":") else None
            courant = None
            continue
        if section is None:
            continue

        if nue.startswith("- "):
            reste = nue[2:].strip()
            cle, separateur, valeur = reste.partition(":")
            if separateur and CLE.match(cle.strip()):
                courant = {cle.strip(): _nettoyer(valeur)}
                _ajouter(out, section, courant)
            else:
                courant = None
                _ajouter(out, section, _nettoyer(reste))
            continue

        cle, separateur, valeur = nue.partition(":")
        if not separateur or not CLE.match(cle.strip()):
            continue
        if courant is not None:
            courant[cle.strip()] = _nettoyer(valeur)
            continue
        courante = out.get(section)
        if courante is None:
            courante = out[section] = {}
        if isinstance(courante, dict):
            courante[cle.strip()] = _nettoyer(valeur)

    return {nom: contenu for nom, contenu in out.items() if contenu}


def _ajouter(out: dict[str, Section], section: str, element) -> None:
    courante = out.get(section)
    if not isinstance(courante, list):
        courante = out[section] = []
    courante.append(element)


def charger(chemin: str | Path | None) -> tuple[dict[str, Section], str]:
    """Lit un fichier de regles. Renvoie ses sections et sa provenance.

    Absent, illisible ou vide : des sections vides et la provenance `defauts`.
    L'appelant retombe alors sur ses valeurs par defaut — GK se degrade, il
    n'echoue pas, y compris sur une edition maladroite d'un fichier de regles.
    """
    if chemin is None:
        return {}, "defauts"
    p = Path(chemin)
    if not p.is_file():
        return {}, "defauts"
    try:
        sections = lire_sections(p.read_text(encoding="utf-8"))
    except Exception:
        return {}, "defauts"
    if not sections:
        return {}, "defauts"
    return sections, str(p)


def compiler(motifs: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Compile une liste de motifs en ignorant les invalides.

    Meme choix que `patterns.py` : un motif invalide est ignore et non fatal, le
    detecteur devant survivre a une edition maladroite du fichier.
    """
    out: list[tuple[str, re.Pattern[str]]] = []
    for motif in motifs:
        if not isinstance(motif, str) or not motif.strip():
            continue
        try:
            out.append((motif, re.compile(motif)))
        except re.error:
            continue
    return out
