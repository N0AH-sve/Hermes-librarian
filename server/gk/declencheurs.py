"""Le tri des declencheurs : positif d'un cote, contre-indication de l'autre.

Ce module **mesure avant de trancher**, et c'est tout l'objet de sa premiere
version. La colonne a poids negatif est une jolie idee ; elle ne vaut que si les
skills declarent reellement quand NE PAS les charger. Le relevé de production du
2026-10-22 dit que 65 skills sur 182 ont une section de declencheurs. Reste a
savoir combien y mettent une contre-indication : si c'est dix, la colonne coute
plus qu'elle ne rapporte et on l'abandonne avec un chiffre ; si c'est quarante,
elle est essentielle.

Deux formes de negation, et il faut les compter separement :

* **structurelle** — la contre-indication a son propre titre (`Ne pas charger
  si`, `When not to use`). C'est le cas propre, celui que le gabarit
  `gm-skill-template` doit generaliser ;
* **en prose** — elle est noyee dans la section positive, et il faut la deviner
  ligne par ligne sur des marqueurs. C'est un reniflage, donc chaque marqueur
  est compte a part : un marqueur bruyant doit se voir et se retirer.

Et une mesure qui n'etait dans aucune des huit etapes : **combien de sections
citent un autre skill**. Un `## Declencheurs` est ecrit pour un humain, et il
cite volontiers ses voisins pour s'en distinguer (« ne pas charger pour une
commande client : voir gm-presta-catalogue »). Indexees comme texte positif, ces
citations font remonter un skill sur les demandes de son voisin. C'est le risque
de precision de l'ajout des declencheurs, et il se chiffre ici.
"""

from __future__ import annotations

from collections import Counter

from .patterns import fold
from .store import Store

# Un titre qui porte deja la contre-indication : tout son texte est negatif.
TITRES_NEGATIFS = (
    "ne pas charger",
    "ne jamais charger",
    "when not to use",
    "when not to load",
    "do not load",
    "don t load",
)

# Marqueurs de contre-indication en prose, comptes separement pour qu'un
# marqueur bruyant se voie.
#
# CORRIGE apres le premier relevé reel : les marqueurs se CHEVAUCHENT. Une ligne
# portant « ne jamais » comptait a la fois pour `ne jamais` et pour `jamais`, et
# le relevé du 2026-10-22 donnait `jamais: 35` contre `ne jamais: 13` -- un
# chiffre gonfle par son propre sous-marqueur. Seul le marqueur le plus long est
# retenu par ligne.
MARQUEURS = (
    "ne pas",
    "ne jamais",
    "n utilise pas",
    "n appelle pas",
    "ne convient pas",
    "ne s applique pas",
    "ne me charge pas",
    "pas pour",
    "contre-indication",
    "a eviter",
    "sauf",
    "jamais",
    "do not",
    "don t",
    "never",
    "avoid",
    "not for",
    "except",
)

# Marqueurs FAIBLES : presents dans des tournures qui ne sont pas des
# contre-indications. « le client n'a jamais commande » est une condition
# positive ; « sauf le week-end » precise un declencheur au lieu de l'exclure.
# Le comptage est rendu avec ET sans eux, pour que la decision connaisse sa
# propre marge d'erreur.
MARQUEURS_FAIBLES = ("jamais", "sauf", "except")

# Marqueurs de RENVOI : ce qui distingue un vrai renvoi vers un skill voisin de
# la simple mention d'un outil du metier.
#
# Releve du 2026-10-22 : la premiere mesure comptait 16 skills « citant un autre
# skill », mais `pennylane`, `prestashop`, `linear`, `notion` et `himalaya` sont
# des OUTILS qui ont un skill portant leur nom. Les mentionner n'est pas se
# distinguer d'un concurrent, c'est nommer son domaine. Confondre les deux
# surestime le risque de precision.
MARQUEURS_RENVOI = (
    "voir ", "cf ", " vs ", "plutot", "a la place", "preferer",
    "see ", "instead", "use ", "prefer ", "rather than",
)

# Les lignes entre guillemets sont des EXEMPLES DE DEMANDE, pas de la prose.
# Releve du 2026-10-22 : les declencheurs GM sont redigees comme des citations
# de l'utilisateur -- « point dettes », « ou en sont les factures fournisseurs
# en attente ». Donc du texte de la meme forme que les requetes, ce qui en fait
# le signal le plus fort possible pour une recherche lexicale. Les guillemets
# sont un signal structurel, pas une devinette.
GUILLEMETS = ("\u00ab", "\u00bb", "\u201c", "\u201d", '"')

# Un extrait de section ne sert qu'au diagnostic, et il est borne.
MAX_EXEMPLE_CHARS = 120


def lignes_citees(texte: str) -> list[str]:
    """Les lignes qui portent des guillemets, donc des exemples de demande."""
    return [
        ligne for ligne in (texte or "").splitlines()
        if any(g in ligne for g in GUILLEMETS)
    ]


def est_un_renvoi(ligne: str) -> bool:
    plie = fold(ligne or "")
    return any(m.strip() and m.strip() in plie for m in MARQUEURS_RENVOI)


def titre_negatif(heading: str) -> bool:
    plie = fold(heading or "")
    return any(plie.startswith(t) for t in TITRES_NEGATIFS)


def marqueurs_de(ligne: str) -> list[str]:
    """Les marqueurs d'une ligne, **sans chevauchement**.

    Un marqueur qui est un sous-mot d'un autre marqueur trouve est ecarte :
    « ne jamais charger » rend `ne jamais` et non pas aussi `jamais`. Sans cela
    le comptage se gonfle tout seul, ce qu'un premier relevé a montre.
    """
    plie = fold(ligne or "")
    trouves = [m for m in MARQUEURS if m in plie]
    return [
        m for m in trouves
        if not any(autre != m and m in autre for autre in trouves)
    ]


def marqueurs_forts(ligne: str) -> list[str]:
    return [m for m in marqueurs_de(ligne) if m not in MARQUEURS_FAIBLES]


def decouper(heading: str, texte: str, strict: bool = False) -> tuple[str, str, Counter]:
    """Rend `(positif, negatif, marqueurs_vus)`.

    Un titre deja negatif envoie tout son texte du cote negatif. Sinon le
    decoupage est **ligne par ligne** : une ligne qui porte un marqueur passe en
    negatif, les autres restent positives. Grossier a dessein — c'est une mesure
    avant d'etre un mecanisme, et le resultat doit rester lisible a la main.

    `strict=True` ignore les marqueurs faibles (`jamais`, `sauf`, `except`), qui
    apparaissent dans des tournures positives. L'ecart entre les deux comptages
    est la marge d'erreur de la mesure, et elle doit etre dite.
    """
    reperer = marqueurs_forts if strict else marqueurs_de
    vus: Counter = Counter()
    if titre_negatif(heading):
        for ligne in (texte or "").splitlines():
            vus.update(marqueurs_de(ligne))
        return "", texte or "", vus
    positif: list[str] = []
    negatif: list[str] = []
    for ligne in (texte or "").splitlines():
        trouves = reperer(ligne)
        if trouves:
            vus.update(trouves)
            negatif.append(ligne)
        else:
            positif.append(ligne)
    return "\n".join(positif).strip(), "\n".join(negatif).strip(), vus


def mesurer(store: Store, exemples: bool = False) -> dict:
    """Mesure ce que le tri donnerait, sans rien ecrire.

    Lecture seule sur la base : aucune colonne derivee n'est creee ici. Le but
    est de decider si la colonne negative merite d'exister.
    """
    sections = store.query(
        "SELECT path, heading, text FROM skill_sections ORDER BY path, heading"
    )
    noms = {}
    for row in store.query(
        "SELECT path, name FROM skills WHERE missing_since IS NULL AND name <> ''"
    ):
        noms[row["path"]] = row["name"]
    noms_plies = {fold(n): n for n in noms.values() if len(n) >= 6}

    par_skill: dict[str, dict] = {}
    strict_par_skill: set[str] = set()
    marqueurs: Counter = Counter()
    titres: Counter = Counter()
    echantillons: list[str] = []
    octets_positifs = octets_negatifs = 0
    sections_titre_negatif = 0

    for row in sections:
        path, heading, texte = row["path"], row["heading"], row["text"] or ""
        titres[fold(heading)] += 1
        positif, negatif, vus = decouper(heading, texte)
        marqueurs.update(vus)
        octets_positifs += len(positif.encode("utf-8"))
        octets_negatifs += len(negatif.encode("utf-8"))
        etat = par_skill.setdefault(
            path,
            {
                "negatif_structurel": False, "negatif_prose": False,
                "cite": set(), "renvoi": set(), "lignes_citees": 0,
            },
        )
        # Le meme decoupage, sans les marqueurs faibles : l'ecart entre les
        # deux est la marge d'erreur de la mesure.
        _, negatif_strict, _ = decouper(heading, texte, strict=True)
        if negatif_strict:
            strict_par_skill.add(path)
        if titre_negatif(heading):
            sections_titre_negatif += 1
            etat["negatif_structurel"] = True
        elif negatif:
            etat["negatif_prose"] = True
            if exemples and len(echantillons) < 8:
                echantillons.append(negatif.splitlines()[0].strip()[:MAX_EXEMPLE_CHARS])
        # Exemples de demande entre guillemets : le signal le plus fort.
        citees = lignes_citees(texte)
        if citees:
            etat["lignes_citees"] = etat.get("lignes_citees", 0) + len(citees)
        # Citations d'autres skills. Deux niveaux, parce que les confondre
        # surestime le risque : la simple mention, et le RENVOI explicite.
        moi = fold(noms.get(path, ""))
        for ligne in (texte or "").splitlines():
            plie_ligne = fold(ligne)
            renvoi = est_un_renvoi(ligne)
            for nom_plie, nom in noms_plies.items():
                if nom_plie == moi or nom_plie not in plie_ligne:
                    continue
                etat["cite"].add(nom)
                if renvoi or ligne in negatif.splitlines():
                    etat["renvoi"].add(nom)

    total_skills = int(
        store.one("SELECT COUNT(*) c FROM skills WHERE missing_since IS NULL")["c"]
    )
    avec_section = len(par_skill)
    structurel = sum(1 for e in par_skill.values() if e["negatif_structurel"])
    prose = sum(
        1 for e in par_skill.values()
        if e["negatif_prose"] and not e["negatif_structurel"]
    )
    citants = {p: sorted(e["cite"]) for p, e in par_skill.items() if e["cite"]}
    renvois = {p: sorted(e["renvoi"]) for p, e in par_skill.items() if e["renvoi"]}
    avec_exemples = sum(1 for e in par_skill.values() if e.get("lignes_citees"))
    lignes_exemples = sum(e.get("lignes_citees", 0) for e in par_skill.values())
    prose_strict = sum(
        1 for p, e in par_skill.items()
        if p in strict_par_skill and not e["negatif_structurel"]
    )
    return {
        "skills_au_catalogue": total_skills,
        "skills_avec_declencheurs": avec_section,
        "sections": len(sections),
        "negatif_structurel": structurel,
        "negatif_en_prose": prose,
        # Hors marqueurs faibles (`jamais`, `sauf`, `except`), qui apparaissent
        # dans des tournures positives. C'est ce comptage qui porte la decision.
        "negatif_en_prose_strict": prose_strict,
        "sans_contre_indication": avec_section - structurel - prose,
        "sections_a_titre_negatif": sections_titre_negatif,
        "octets_positifs": octets_positifs,
        "octets_negatifs": octets_negatifs,
        "par_marqueur": dict(marqueurs.most_common()),
        "titres_rencontres": dict(titres.most_common(12)),
        # Deux niveaux : la mention (large, qui inclut les noms d'outils) et le
        # renvoi explicite (strict, qui est le vrai risque de precision).
        "skills_mentionnant_un_autre_skill": len(citants),
        "skills_avec_renvoi_explicite": len(renvois),
        "citations": dict(sorted(citants.items())[:10]),
        "renvois": dict(sorted(renvois.items())[:10]),
        # Les exemples de demande entre guillemets : le signal le plus fort.
        "skills_avec_exemples_cites": avec_exemples,
        "lignes_entre_guillemets": lignes_exemples,
        "exemples": echantillons,
    }


def lecture(m: dict) -> list[str]:
    """Ce que les chiffres decident, dit par le programme plutot que negocie
    apres coup."""
    lignes: list[str] = []
    avec = m["skills_avec_declencheurs"] or 1
    total = m["skills_au_catalogue"] or 1
    # La decision se prend sur le comptage STRICT : le comptage large inclut
    # `jamais`, `sauf` et `except`, qui apparaissent dans des tournures
    # positives. Annoncer le chiffre flatteur serait se tromper soi-meme.
    large = m["negatif_structurel"] + m["negatif_en_prose"]
    negatifs = m["negatif_structurel"] + m.get("negatif_en_prose_strict", m["negatif_en_prose"])
    part = 100.0 * negatifs / avec
    part_catalogue = 100.0 * negatifs / total
    lignes.append(
        f"Contre-indication : {negatifs} skills sur {m['skills_avec_declencheurs']} "
        f"qui ont des declencheurs ({part:.0f} %), soit {part_catalogue:.0f} % du "
        f"catalogue. Au plus {large} en comptant les marqueurs faibles."
    )
    if part_catalogue < 10:
        lignes.append(
            "=> La colonne a poids negatif couterait plus qu'elle ne rapporte. "
            "L'abandonner, et la rouvrir si le gabarit fait monter ce chiffre."
        )
    elif part_catalogue < 25:
        lignes.append(
            "=> Marginale. La garder seulement si elle est gratuite : un champ "
            "de plus et un poids dans la requete, aucun mecanisme separe."
        )
    else:
        lignes.append(
            "=> Elle vaut son code. Et elle devient necessaire des que les "
            "declencheurs sont indexes, sinon une ligne « ne pas charger pour X » "
            "fait remonter le skill sur X."
        )
    if m["negatif_structurel"] == 0 and m["negatif_en_prose"] > 0:
        lignes.append(
            "Aucune contre-indication structurelle : tout repose sur le reniflage "
            "de marqueurs. C'est l'argument pour que gm-skill-template impose un "
            "titre dedie."
        )
    renvois = m.get("skills_avec_renvoi_explicite")
    citants = m.get("skills_mentionnant_un_autre_skill", m.get("skills_citant_un_autre_skill", 0))
    if renvois:
        lignes.append(
            f"{renvois} skills renvoient explicitement a un voisin "
            f"({100.0 * renvois / avec:.0f} %), sur {citants} qui en mentionnent un. "
            "Seuls les renvois sont le risque de precision : mentionner "
            "`pennylane` ou `prestashop`, c'est nommer son domaine, pas se "
            "distinguer d'un concurrent."
        )
    elif citants:
        lignes.append(
            f"{citants} skills mentionnent un autre skill, aucun avec un renvoi "
            "explicite : ce sont des noms d'outils, pas un risque de precision."
        )
    exemples = m.get("skills_avec_exemples_cites")
    if exemples:
        lignes.append(
            f"{exemples} skills sur {m['skills_avec_declencheurs']} redigent leurs "
            f"declencheurs comme des citations de l'utilisateur "
            f"({m.get('lignes_entre_guillemets', 0)} lignes entre guillemets). "
            "Ce texte a la meme forme que les demandes : c'est le signal le plus "
            "fort possible, et il merite le poids le plus eleve."
        )
    return lignes
