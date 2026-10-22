#!/usr/bin/env python3
"""Mesure la structure reelle des SKILL.md, avant d'ecrire la phase 2.

Toute la conception de la phase 2 repose sur une hypothese : que les skills
suivent le squelette (`## Declencheurs`, `## Regle`, `## Procedure`, `## Pieges`,
`## Verification`, `## References`). Si c'est vrai pour la grande majorite des
534, ponderer par rubrique est le bon plan et le « quand NE PAS charger » peut
se soustraire. Si c'est vrai pour la moitie, c'est une fantaisie, et il faut
indexer le corps entier avec un bonus sur le titre.

Personne ne sait lequel des deux. Ce script le dit, et ne fait que cela.

**Lecture seule.** Il ouvre des fichiers, il n'en ecrit aucun, il ne touche ni
la configuration ni la base. On peut le lancer sur la production sans precaution.

**Il n'imprime pas le contenu des skills**, seulement des titres de section et
des comptages : la sortie est destinee a etre recopiee dans une conversation.
`--exemples` ajoute quelques lignes tronquees, a n'utiliser qu'en connaissance
de cause.

Usage, dans le conteneur de l'agent :

    docker compose -f ~/next_hermes/compose.yaml exec -T hermes python3 - \\
      < ~/greenknowledge/tools/mesurer-rubriques.py

Options (apres `-` il faut les passer autrement ; depuis un fichier copie :
`python3 mesurer-rubriques.py --racine /opt/data/skills --json`).
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

RACINE_DEFAUT = "/opt/data/skills"

# Les six rubriques du squelette, avec les variantes rencontrees dans les quatre
# familles de skills. Un titre est classe s'il COMMENCE par une de ces formes :
# le squelette admet une glose derriere le mot-cle.
RUBRIQUES: dict[str, tuple[str, ...]] = {
    "declencheurs": ("declencheur", "quand charger", "quand utiliser"),
    "regle": ("regle",),
    "procedure": ("procedure", "marche a suivre", "etapes"),
    "pieges": ("piege",),
    "verification": ("verification", "verifier", "preuve"),
    "references": ("reference", "annexe"),
}

# Marqueurs de contre-indication. C'est sur eux que repose la colonne a poids
# negatif : si `Declencheurs` ne contient presque jamais de negation, la moitie
# « quand NE PAS charger » n'existe pas dans les faits et la colonne non plus.
NEGATIONS = (
    "ne pas", "n utilise pas", "n appelle pas", "ne jamais", "jamais",
    "sauf", "pas pour", "ne me charge pas", "contre-indication", "a eviter",
    "eviter", "ne convient pas", "ne s applique pas",
)


def plier(texte: str) -> str:
    """Minuscules, accents retires, apostrophes et espaces normalises.

    Meme pliage que le detecteur de motifs de la phase 1 : les skills sont
    ecrits en francais, avec des accents inconstants.
    """
    texte = texte.replace("’", " ").replace("'", " ")
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return " ".join(texte.lower().split())


def classer(titre_plie: str) -> str | None:
    for rubrique, formes in RUBRIQUES.items():
        for forme in formes:
            if titre_plie.startswith(forme):
                return rubrique
    return None


def decouper(texte: str) -> tuple[list[tuple[int, str, str]], Counter]:
    """Rend les sections et le compte des niveaux de titre.

    Les blocs de code sont suivis : un `##` dans un bloc shell ou YAML est un
    commentaire, pas un titre. Sans cela le comptage serait faux sur des skills
    qui sont precisement ceux qui portent des procedures.
    """
    sections: list[tuple[int, str, str]] = []  # (niveau, titre, corps)
    niveaux: Counter = Counter()
    dans_bloc = False
    niveau_courant, titre_courant = 0, ""
    corps: list[str] = []
    for ligne in texte.splitlines():
        depouillee = ligne.strip()
        if depouillee.startswith("```") or depouillee.startswith("~~~"):
            dans_bloc = not dans_bloc
            corps.append(ligne)
            continue
        if not dans_bloc and depouillee.startswith("#"):
            diese = len(depouillee) - len(depouillee.lstrip("#"))
            reste = depouillee[diese:]
            # `#commentaire` sans espace n'est pas un titre Markdown.
            if 1 <= diese <= 6 and (reste.startswith(" ") or not reste):
                niveaux[diese] += 1
                if titre_courant or corps:
                    sections.append((niveau_courant, titre_courant, "\n".join(corps)))
                niveau_courant, titre_courant, corps = diese, reste.strip(), []
                continue
        corps.append(ligne)
    if titre_courant or corps:
        sections.append((niveau_courant, titre_courant, "\n".join(corps)))
    return sections, niveaux


def frontmatter(texte: str) -> set[str]:
    """Les cles de premier niveau du frontmatter, sans analyseur YAML."""
    if not texte.startswith("---"):
        return set()
    lignes = texte.splitlines()
    cles = set()
    for ligne in lignes[1:]:
        if ligne.strip() == "---":
            break
        if ligne[:1] not in (" ", "\t", "-", "") and ":" in ligne:
            cles.add(ligne.split(":", 1)[0].strip())
    return cles


def mesurer(racine: Path, exemples: bool) -> dict:
    fichiers = sorted(p for p in racine.rglob("SKILL.md") if "__pycache__" not in p.parts)

    titres: Counter = Counter()          # titres `##` plies, tous fichiers confondus
    niveaux_totaux: Counter = Counter()
    presence: Counter = Counter()        # fichiers contenant chaque rubrique
    octets: Counter = Counter()          # octets par rubrique, + hors_rubrique
    profondeurs: Counter = Counter()
    cles_fm: Counter = Counter()
    sans_titre: list[str] = []
    squelette_complet = 0
    avec_negation = 0
    declencheurs_vus = 0
    illisibles: list[str] = []
    echantillons: list[str] = []

    for chemin in fichiers:
        try:
            texte = chemin.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            illisibles.append(f"{chemin}: {exc}")
            continue
        profondeurs[len(chemin.relative_to(racine).parts)] += 1
        cles_fm.update(frontmatter(texte))
        sections, niveaux = decouper(texte)
        niveaux_totaux.update(niveaux)

        vues: set[str] = set()
        if not niveaux:
            sans_titre.append(str(chemin.relative_to(racine)))
        for niveau, titre, corps in sections:
            if niveau != 2 or not titre:
                # Distinguer les deux : le frontmatter et le preambule sont
                # incompressibles, un `###` mal choisi est un defaut de forme.
                # Les confondre cacherait lequel des deux pese.
                cle = "avant_premier_titre" if niveau == 0 else "autre_niveau"
                octets[cle] += len(corps.encode("utf-8"))
                continue
            plie = plier(titre)
            titres[plie] += 1
            rubrique = classer(plie)
            octets[rubrique or "titre_non_standard"] += len(corps.encode("utf-8"))
            if rubrique:
                vues.add(rubrique)
            if rubrique == "declencheurs":
                declencheurs_vus += 1
                corps_plie = plier(corps)
                if any(n in corps_plie for n in NEGATIONS):
                    avec_negation += 1
                    if exemples and len(echantillons) < 5:
                        for ligne in corps.splitlines():
                            if any(n in plier(ligne) for n in NEGATIONS):
                                echantillons.append(ligne.strip()[:120])
                                break
        presence.update(vues)
        if len(vues) == len(RUBRIQUES):
            squelette_complet += 1

    total = len(fichiers) - len(illisibles)
    rares = sum(1 for _, n in titres.items() if n <= 2)
    return {
        "racine": str(racine),
        "fichiers_trouves": len(fichiers),
        "fichiers_lus": total,
        "illisibles": illisibles,
        "profondeur_relative": dict(sorted(profondeurs.items())),
        "niveaux_de_titre": dict(sorted(niveaux_totaux.items())),
        "frontmatter": dict(cles_fm.most_common(12)),
        "rubriques": {r: presence.get(r, 0) for r in RUBRIQUES},
        "squelette_complet": squelette_complet,
        "sans_aucun_titre": len(sans_titre),
        "exemples_sans_titre": sans_titre[:5],
        "titres_distincts": len(titres),
        "titres_rares": rares,
        "titres_frequents": titres.most_common(25),
        "declencheurs_sections": declencheurs_vus,
        "declencheurs_avec_negation": avec_negation,
        "octets_par_rubrique": dict(octets.most_common()),
        "echantillons_negation": echantillons,
    }


def pourcent(n: int, total: int) -> str:
    return f"{100.0 * n / total:.0f} %" if total else "—"


def rapport(m: dict) -> None:
    total = m["fichiers_lus"] or 1
    p = print
    p(f"Racine : {m['racine']}")
    p(f"SKILL.md trouves : {m['fichiers_trouves']}, lus : {m['fichiers_lus']}")
    if m["illisibles"]:
        p(f"ILLISIBLES ({len(m['illisibles'])}) : {m['illisibles'][:3]}")
    p(f"Profondeur sous la racine : {m['profondeur_relative']}")
    p(f"Niveaux de titre (# : n) : {m['niveaux_de_titre']}")
    p("")
    p("-- Les six rubriques du squelette, en nombre de fichiers ----------------")
    for rubrique, n in m["rubriques"].items():
        p(f"  {rubrique:<14} {n:>4}   {pourcent(n, total)}")
    p(f"  {'LES SIX':<14} {m['squelette_complet']:>4}   "
      f"{pourcent(m['squelette_complet'], total)}")
    p(f"  sans aucun titre : {m['sans_aucun_titre']} "
      f"({pourcent(m['sans_aucun_titre'], total)}) {m['exemples_sans_titre']}")
    p("")
    p("-- Le signal negatif, dont depend la colonne a poids negatif ------------")
    d = m["declencheurs_sections"]
    p(f"  sections Declencheurs : {d}")
    p(f"  dont avec une contre-indication : {m['declencheurs_avec_negation']} "
      f"({pourcent(m['declencheurs_avec_negation'], d or 1)} de celles-ci)")
    for ligne in m["echantillons_negation"]:
        p(f"    | {ligne}")
    p("")
    p("-- Vocabulaire des titres `##` ------------------------------------------")
    p(f"  titres distincts : {m['titres_distincts']}, "
      f"dont vus 1 ou 2 fois : {m['titres_rares']}")
    for titre, n in m["titres_frequents"]:
        p(f"  {n:>4}  {titre[:70]}")
    p("")
    p("-- Budget en octets par rubrique ----------------------------------------")
    somme = sum(m["octets_par_rubrique"].values()) or 1
    for rubrique, n in m["octets_par_rubrique"].items():
        p(f"  {rubrique:<20} {n/1024:>9.0f} Ko   {pourcent(n, somme)}")
    p(f"  {'TOTAL':<20} {somme/1024:>9.0f} Ko")
    p("")
    p("-- Lecture --------------------------------------------------------------")
    sq = 100.0 * m["squelette_complet"] / total
    decl = 100.0 * m["rubriques"].get("declencheurs", 0) / total
    p(f"  Squelette complet : {sq:.0f} %. Declencheurs seul : {decl:.0f} %.")
    if decl >= 80:
        p("  => La ponderation par rubrique tient. Declencheurs porte le poids fort,")
        p("     une colonne FTS par rubrique, le reste dans une colonne fourre-tout.")
    elif decl >= 50:
        p("  => Ponderation possible mais la colonne fourre-tout pesera lourd :")
        p("     prevoir que le banc compare avec et sans ponderation.")
    else:
        p("  => La ponderation par rubrique est une fantaisie. Replier sur le corps")
        p("     entier avec un bonus sur nom et description, et le dire a l'etape 6.")
    nd = m["declencheurs_avec_negation"]
    if d and 100.0 * nd / d < 15:
        p(f"  La contre-indication n'apparait que dans {pourcent(nd, d)} des Declencheurs :")
        p("  la colonne a poids negatif couterait plus qu'elle ne rapporte.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mesure la structure des SKILL.md. Lecture seule.")
    ap.add_argument("--racine", default=RACINE_DEFAUT)
    ap.add_argument("--json", action="store_true", help="sortie machine")
    ap.add_argument("--exemples", action="store_true",
                    help="inclut quelques lignes tronquees de contre-indication")
    args = ap.parse_args(argv)

    racine = Path(args.racine)
    if not racine.is_dir():
        print(f"racine introuvable : {racine}", file=sys.stderr)
        return 2
    mesure = mesurer(racine, args.exemples)
    if mesure["fichiers_trouves"] == 0:
        print(f"aucun SKILL.md sous {racine}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(mesure, ensure_ascii=False, indent=2))
    else:
        rapport(mesure)
    return 0


if __name__ == "__main__":
    sys.exit(main())
