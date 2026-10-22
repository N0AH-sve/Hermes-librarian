#!/usr/bin/env python3
"""Mesure la structure reelle des SKILL.md, avant d'ecrire la phase 2.

Toute la ponderation par rubrique de la phase 2 repose sur une hypothese : que
les skills suivent un squelette de rubriques nommees. Si c'est vrai pour la
grande majorite, `Declencheurs` porte le poids fort et le « quand NE PAS
charger » peut se soustraire. Sinon il faut replier sur le corps entier avec un
bonus sur nom et description. Ce script tranche, et ne fait que cela.

**Lecture seule.** Il ouvre des fichiers, il n'en ecrit aucun, il ne touche ni
la configuration ni la base. On peut le lancer sur la production sans precaution.

**Il n'imprime aucun contenu de skill**, seulement des titres de section et des
comptages : sa sortie est faite pour etre recopiee dans une conversation.
`--exemples` ajoute quelques lignes tronquees, a n'utiliser qu'en connaissance
de cause.

Quatre defauts du premier jet, corriges ici parce que chacun faussait la
decision dans le meme sens — faire paraitre la structure plus pauvre qu'elle
n'est :

* `Path.rglob` ne descend pas dans les repertoires qui sont des liens
  symboliques, et se taisait. On marche l'arborescence en suivant les liens,
  avec garde contre les cycles, et on dit combien de liens ont ete suivis ;
* seuls les titres de niveau 2 etaient classes, alors que la structure vit
  largement au niveau 3 ;
* seules des formes francaises etaient reconnues, dans une bibliotheque
  bilingue ou `when to use` est plus frequent que `declencheurs` ;
* un titre prefixe d'un emoji, d'un numero ou de gras ne correspondait a rien.

Usage, dans le conteneur de l'agent :

    docker compose -f ~/next_hermes/compose.yaml exec -T hermes python3 - \\
      < ~/greenknowledge/tools/mesurer-rubriques.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

RACINE_DEFAUT = "/opt/data/skills"
NIVEAUX_CLASSES = (2, 3)  # les niveaux ou une rubrique peut vivre

# Les rubriques cherchees, francais et anglais melanges, parce que la
# bibliotheque l'est. Un titre est classe s'il COMMENCE par une de ces formes :
# le squelette admet une glose derriere le mot-cle.
#
# Cette table se modifie : c'est une hypothese sur les conventions en usage, et
# le rapport liste les titres non classes par poids pour qu'on voie ce qui
# manque au lieu de le deviner.
RUBRIQUES: dict[str, tuple[str, ...]] = {
    "declencheurs": (
        "declencheur", "quand charger", "quand utiliser", "quand l utiliser",
        "when to use", "when to load", "triggers", "trigger", "use when",
    ),
    "regle": ("regle", "les regles", "rules", "rule", "principes", "principles"),
    "procedure": (
        "procedure", "marche a suivre", "etapes", "workflow", "how to run",
        "how to", "quick start", "quickstart", "steps", "usage", "utilisation",
    ),
    "pieges": (
        "piege", "pitfall", "common pitfall", "troubleshooting", "erreurs",
        "gotcha", "anti-pattern", "antipattern",
    ),
    "verification": (
        "verification", "verifier", "preuve", "verify", "checks", "checklist",
        "validation",
    ),
    "references": (
        "reference", "annexe", "ou lire quoi", "fichiers", "files", "voir aussi",
        "see also",
    ),
}

# Marqueurs de contre-indication. C'est sur eux que repose la colonne a poids
# negatif : si les skills ne declarent presque jamais quand NE PAS les charger,
# la colonne couterait plus qu'elle ne rapporte.
NEGATIONS = (
    "ne pas", "n utilise pas", "n appelle pas", "ne jamais", "jamais",
    "sauf", "pas pour", "ne me charge pas", "contre-indication", "a eviter",
    "eviter", "ne convient pas", "ne s applique pas",
    "do not", "don t", "never", "avoid", "not for", "except",
)

MOTS_FR = (" le ", " la ", " les ", " des ", " une ", " pour ", " avec ",
           " dans ", " est ", " que ", " qui ", " sur ", " du ", " au ")
MOTS_EN = (" the ", " and ", " for ", " with ", " this ", " that ", " you ",
           " are ", " of ", " to ", " is ", " in ", " on ", " be ")

# Prefixes decoratifs a retirer avant de classer : emoji, numerotation, gras,
# puces. `## 📚 Historique` et `## 3. Procédure` doivent se classer comme
# `historique` et `procedure`.
PREFIXE_DECORATIF = re.compile(r"^[^0-9a-zA-ZÀ-ɏ]+|^\d+[\.\)]\s*")


def plier(texte: str) -> str:
    """Minuscules, accents retires, apostrophes et espaces normalises."""
    texte = texte.replace("’", " ").replace("'", " ")
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return " ".join(texte.lower().split())


def normaliser_titre(titre: str) -> str:
    t = titre.strip().strip("*_`#").strip()
    precedent = None
    while t and t != precedent:
        precedent = t
        t = PREFIXE_DECORATIF.sub("", t).strip()
    return plier(t)


def classer(titre_plie: str) -> str | None:
    for rubrique, formes in RUBRIQUES.items():
        for forme in formes:
            if titre_plie.startswith(forme):
                return rubrique
    return None


def langue(corps_plie: str) -> str:
    fr = sum(corps_plie.count(m) for m in MOTS_FR)
    en = sum(corps_plie.count(m) for m in MOTS_EN)
    if fr == 0 and en == 0:
        return "indetermine"
    if fr >= 2 * max(en, 1):
        return "fr"
    if en >= 2 * max(fr, 1):
        return "en"
    return "mixte"


def decouper(texte: str) -> tuple[list[tuple[int, str, str]], Counter]:
    """Rend les sections et le compte des niveaux de titre.

    Les blocs de code sont suivis : un `##` dans un bloc shell ou YAML est un
    commentaire, pas un titre. Sans cela le comptage serait gonfle, et il le
    serait precisement sur les skills qui portent des procedures.
    """
    sections: list[tuple[int, str, str]] = []
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
    cles = set()
    for ligne in texte.splitlines()[1:]:
        if ligne.strip() == "---":
            break
        if ligne[:1] not in (" ", "\t", "-", "") and ":" in ligne:
            cles.add(ligne.split(":", 1)[0].strip())
    return cles


def trouver(racine: Path) -> tuple[list[Path], dict]:
    """Marche l'arborescence en suivant les liens, avec garde contre les cycles.

    `Path.rglob` ne descend pas dans un repertoire qui est un lien symbolique et
    n'en dit rien : une bibliotheque montee par liens paraitrait deux fois plus
    petite qu'elle n'est, sans aucune erreur. Le diagnostic est rendu avec la
    liste, pour qu'un ecart de comptage s'explique au lieu de se deviner.
    """
    fichiers: list[Path] = []
    vus_reels: set[str] = set()
    diag = {"liens_suivis": 0, "cycles_evites": 0, "illisibles_dossiers": [],
            "doublons_ignores": 0}
    for dossier, sous, noms in os.walk(racine, followlinks=True, onerror=lambda e:
                                       diag["illisibles_dossiers"].append(str(e))):
        reel = os.path.realpath(dossier)
        if reel in vus_reels:
            diag["cycles_evites"] += 1
            sous[:] = []
            continue
        vus_reels.add(reel)
        sous[:] = [s for s in sous if s != "__pycache__"]
        diag["liens_suivis"] += sum(1 for s in sous if os.path.islink(os.path.join(dossier, s)))
        if "SKILL.md" in noms:
            fichiers.append(Path(dossier) / "SKILL.md")
    # Deux chemins qui mènent au meme fichier ne comptent qu'une fois.
    uniques: dict[str, Path] = {}
    for f in fichiers:
        cle = os.path.realpath(f)
        if cle in uniques:
            diag["doublons_ignores"] += 1
            continue
        uniques[cle] = f
    return sorted(uniques.values()), diag


def mesurer(racine: Path, exemples: bool = False) -> dict:
    fichiers, diag = trouver(racine)

    titres: Counter = Counter()
    titres_octets: Counter = Counter()
    niveaux_totaux: Counter = Counter()
    presence: Counter = Counter()
    octets: Counter = Counter()
    profondeurs: Counter = Counter()
    cles_fm: Counter = Counter()
    langues: Counter = Counter()
    non_classes: Counter = Counter()
    squelette_complet = 0
    au_moins_quatre = 0
    sans_titre: list[str] = []
    declencheurs_vus = 0
    avec_negation = 0
    negation_ailleurs = 0
    illisibles: list[str] = []
    echantillons: list[str] = []
    titres_h1 = 0

    for chemin in fichiers:
        try:
            texte = chemin.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            illisibles.append(f"{chemin}: {exc}")
            continue
        try:
            profondeurs[len(chemin.relative_to(racine).parts)] += 1
        except ValueError:  # atteint par un lien sortant de la racine
            profondeurs[0] += 1
        cles_fm.update(frontmatter(texte))
        texte_plie = plier(texte)
        langues[langue(texte_plie)] += 1
        sections, niveaux = decouper(texte)
        niveaux_totaux.update(niveaux)
        titres_h1 += niveaux.get(1, 0)
        if not niveaux:
            sans_titre.append(str(chemin))

        vues: set[str] = set()
        negation_dans_declencheurs = False
        for niveau, titre, corps in sections:
            poids = len(corps.encode("utf-8"))
            if not titre:
                octets["avant_premier_titre"] += poids
                continue
            if niveau not in NIVEAUX_CLASSES:
                octets[f"niveau_{niveau}"] += poids
                continue
            plie = normaliser_titre(titre)
            titres[plie] += 1
            titres_octets[plie] += poids
            rubrique = classer(plie)
            if rubrique:
                vues.add(rubrique)
                octets[rubrique] += poids
                if rubrique == "declencheurs":
                    declencheurs_vus += 1
                    if any(n in plier(corps) for n in NEGATIONS):
                        negation_dans_declencheurs = True
                        if exemples and len(echantillons) < 5:
                            for ligne in corps.splitlines():
                                if any(n in plier(ligne) for n in NEGATIONS):
                                    echantillons.append(ligne.strip()[:120])
                                    break
            else:
                octets["titre_non_classe"] += poids
                non_classes[plie] += poids
        presence.update(vues)
        if negation_dans_declencheurs:
            avec_negation += 1
        elif any(n in texte_plie for n in NEGATIONS):
            # Une contre-indication existe, mais hors d'une rubrique reperee :
            # elle serait perdue par un decoupage qui ne regarde que les titres.
            negation_ailleurs += 1
        if len(vues) == len(RUBRIQUES):
            squelette_complet += 1
        if len(vues) >= 4:
            au_moins_quatre += 1

    total = len(fichiers) - len(illisibles)
    # Concentration du vocabulaire : combien de titres distincts couvrent la
    # moitie des occurrences. C'est ce chiffre qui dit si une colonne par
    # rubrique est envisageable du tout.
    occurrences = sum(titres.values())
    cumul, pour_moitie = 0, 0
    for _, n in titres.most_common():
        cumul += n
        pour_moitie += 1
        if cumul * 2 >= occurrences:
            break
    return {
        "racine": str(racine),
        "diagnostic_parcours": diag,
        "fichiers_trouves": len(fichiers),
        "fichiers_lus": total,
        "illisibles": illisibles,
        "profondeur_relative": dict(sorted(profondeurs.items())),
        "niveaux_de_titre": dict(sorted(niveaux_totaux.items())),
        "titres_h1": titres_h1,
        "langue": dict(langues.most_common()),
        "frontmatter": dict(cles_fm.most_common(12)),
        "rubriques": {r: presence.get(r, 0) for r in RUBRIQUES},
        "squelette_complet": squelette_complet,
        "au_moins_quatre_rubriques": au_moins_quatre,
        "sans_aucun_titre": len(sans_titre),
        "titres_distincts": len(titres),
        "titres_rares": sum(1 for _, n in titres.items() if n <= 2),
        "titres_pour_la_moitie": pour_moitie,
        "titres_frequents": titres.most_common(25),
        "non_classes_par_poids": [(t, round(o / 1024)) for t, o in non_classes.most_common(20)],
        "declencheurs_sections": declencheurs_vus,
        "fichiers_avec_negation_dans_declencheurs": avec_negation,
        "fichiers_avec_negation_ailleurs": negation_ailleurs,
        "octets_par_rubrique": dict(octets.most_common()),
        "echantillons_negation": echantillons,
    }


def pourcent(n: int, total: int) -> str:
    return f"{100.0 * n / total:.0f} %" if total else "—"


def rapport(m: dict, attendu: int | None = None) -> None:
    total = m["fichiers_lus"] or 1
    p = print
    p(f"Racine : {m['racine']}")
    p(f"SKILL.md trouves : {m['fichiers_trouves']}, lus : {m['fichiers_lus']}")
    d = m["diagnostic_parcours"]
    p(f"Parcours : {d['liens_suivis']} lien(s) suivi(s), "
      f"{d['cycles_evites']} cycle(s) evite(s), "
      f"{d['doublons_ignores']} doublon(s) de chemin ignore(s)")
    if d["illisibles_dossiers"]:
        p(f"  DOSSIERS ILLISIBLES : {d['illisibles_dossiers'][:3]}")
    if m["illisibles"]:
        p(f"  FICHIERS ILLISIBLES ({len(m['illisibles'])}) : {m['illisibles'][:3]}")
    if attendu is not None:
        etat = "CONCORDE" if attendu == m["fichiers_trouves"] else "ECART"
        p(f"Attendu (passe en option) : {attendu} -> {etat}")
    p(f"Profondeur sous la racine : {m['profondeur_relative']}")
    p(f"Niveaux de titre (# : n) : {m['niveaux_de_titre']}")
    p(f"Langue des corps : {m['langue']}")
    p("")
    p("-- Rubriques reperees, en nombre de fichiers (niveaux 2 et 3) -----------")
    for rubrique, n in m["rubriques"].items():
        p(f"  {rubrique:<14} {n:>4}   {pourcent(n, total)}")
    p(f"  {'LES SIX':<14} {m['squelette_complet']:>4}   "
      f"{pourcent(m['squelette_complet'], total)}")
    p(f"  {'>= QUATRE':<14} {m['au_moins_quatre_rubriques']:>4}   "
      f"{pourcent(m['au_moins_quatre_rubriques'], total)}")
    p(f"  sans aucun titre : {m['sans_aucun_titre']}")
    p("")
    p("-- Le signal negatif, dont depend la colonne a poids negatif ------------")
    p(f"  sections Declencheurs : {m['declencheurs_sections']}")
    p(f"  fichiers avec contre-indication DANS Declencheurs : "
      f"{m['fichiers_avec_negation_dans_declencheurs']} "
      f"({pourcent(m['fichiers_avec_negation_dans_declencheurs'], total)})")
    p(f"  fichiers avec contre-indication AILLEURS : "
      f"{m['fichiers_avec_negation_ailleurs']} "
      f"({pourcent(m['fichiers_avec_negation_ailleurs'], total)})")
    for ligne in m["echantillons_negation"]:
        p(f"    | {ligne}")
    p("")
    p("-- Vocabulaire des titres -----------------------------------------------")
    p(f"  distincts : {m['titres_distincts']}, vus 1 ou 2 fois : {m['titres_rares']}, "
      f"il en faut {m['titres_pour_la_moitie']} pour couvrir la moitie des occurrences")
    for titre, n in m["titres_frequents"]:
        p(f"  {n:>4}  {titre[:70]}")
    p("")
    p("-- Titres non classes, par poids (les conventions qui nous manquent) ----")
    for titre, ko in m["non_classes_par_poids"]:
        p(f"  {ko:>5} Ko  {titre[:70]}")
    p("")
    p("-- Budget en octets ------------------------------------------------------")
    somme = sum(m["octets_par_rubrique"].values()) or 1
    for rubrique, n in m["octets_par_rubrique"].items():
        p(f"  {rubrique:<22} {n/1024:>9.0f} Ko   {pourcent(n, somme)}")
    p(f"  {'TOTAL':<22} {somme/1024:>9.0f} Ko")
    p("")
    p("-- Lecture --------------------------------------------------------------")
    if attendu is not None and attendu != m["fichiers_trouves"]:
        p("  ECART DE DENOMINATEUR : ne rien conclure avant de l'avoir explique.")
        return
    decl = 100.0 * m["rubriques"].get("declencheurs", 0) / total
    quatre = 100.0 * m["au_moins_quatre_rubriques"] / total
    p(f"  Declencheurs : {decl:.0f} %. Au moins quatre rubriques : {quatre:.0f} %.")
    if decl >= 80:
        p("  => La ponderation par rubrique tient. Declencheurs porte le poids fort,")
        p("     une colonne FTS par rubrique, le reste dans une colonne fourre-tout.")
    elif decl >= 50:
        p("  => Ponderation possible, mais le fourre-tout pesera lourd : le banc doit")
        p("     comparer avec et sans ponderation avant qu'on tranche.")
    else:
        p("  => Pas de squelette exploitable. Replier sur le corps entier avec un")
        p("     bonus sur nom et description, et corriger l'etape de conception 6.")
    nd = m["fichiers_avec_negation_dans_declencheurs"]
    if m["declencheurs_sections"] and 100.0 * nd / total < 15:
        p(f"  La contre-indication structuree ne touche que {pourcent(nd, total)} des")
        p("  fichiers : la colonne a poids negatif couterait plus qu'elle ne rapporte.")
    if m["langue"].get("en", 0) > 0.2 * total:
        p(f"  Bibliotheque bilingue ({m['langue']}) : un stemmer francais seul est")
        p("  un mauvais choix. Tokenizer neutre, ou deux analyses.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mesure la structure des SKILL.md. Lecture seule.")
    ap.add_argument("--racine", default=RACINE_DEFAUT)
    ap.add_argument("--attendu", type=int, default=None,
                    help="nombre de SKILL.md attendu (p. ex. celui de find) ; "
                         "en cas d'ecart le rapport refuse de conclure")
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
        rapport(mesure, args.attendu)
    return 0


if __name__ == "__main__":
    sys.exit(main())
