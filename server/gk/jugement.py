"""L'echantillon a juger, et sa relecture.

Tout est bloque sur le meme manque : **aucune verite terrain sur des demandes
reelles**. Ni le seuil de confiance, ni le vrai rappel, ni la decision
d'injecter un brief. Les corrections de la phase 1 en donneraient, mais il y en
a six.

Le moyen court est de juger a la main : cinquante demandes reelles, cinq skills
proposes pour chacune, et un humain qui dit lequel est le bon. Une heure de
travail donne le rappel@1 et @5 reels, le seuil refixe sur les vraies donnees,
et donc le feu vert ou le refus pour la phase 3.

Le format est volontairement pauvre -- du texte qu'on edite dans n'importe quel
editeur -- parce qu'un format riche coute une interface, et qu'une interface
coute plus que l'heure qu'elle economise.
"""

from __future__ import annotations

import re

from .recherche import Index, contexte_de_session
from .store import Store

MARQUEUR = "REPONSE:"
# Un echantillon stratifie par longueur : les demandes courtes et longues ne
# posent pas le meme probleme, et un tirage simple sur-representerait les
# moyennes.
TRANCHES = ((0, 5), (6, 12), (13, 25), (26, 10_000))


def preparer(store: Store, index: Index, taille: int = 50, par_page: int = 5) -> str:
    """Rend l'echantillon, en texte a annoter."""
    from .recherche import jetons

    rows = store.query(
        "SELECT session_id, text_truncated, ts FROM requests "
        "WHERE text_truncated IS NOT NULL AND text_truncated <> '' "
        "ORDER BY ts DESC LIMIT 1000"
    )
    par_tranche: dict[tuple[int, int], list] = {t: [] for t in TRANCHES}
    for row in rows:
        n = len(jetons(row["text_truncated"]))
        for borne in TRANCHES:
            if borne[0] <= n <= borne[1]:
                par_tranche[borne].append(row)
                break
    choisis: list = []
    quota = max(1, taille // len(TRANCHES))
    for borne in TRANCHES:
        choisis.extend(par_tranche[borne][:quota])
    # On complete avec les plus recentes si une tranche etait maigre.
    if len(choisis) < taille:
        deja = {id(r) for r in choisis}
        choisis.extend(r for r in rows if id(r) not in deja)
    choisis = choisis[:taille]

    lignes = [
        "# Echantillon a juger",
        "",
        "Pour chaque demande : ecrivez apres `REPONSE:` le NUMERO du bon skill,",
        "ou `0` si aucun ne convient, ou `?` si vous ne savez pas.",
        "N'ajoutez rien d'autre : le reste du fichier est relu tel quel.",
        "",
        f"{len(choisis)} demandes, prises parmi les plus recentes et reparties",
        "par longueur, parce qu'une demande courte et une longue ne posent pas le",
        "meme probleme.",
        "",
        "---",
        "",
    ]
    for i, row in enumerate(choisis, start=1):
        demande = row["text_truncated"]
        contexte = contexte_de_session(store, row["session_id"] or "")
        if contexte:
            resultats = index.chercher_avec_contexte(
                demande, contexte=contexte, limite=par_page
            )
        else:
            resultats = index.chercher(demande, limite=par_page)
        lignes.append(f"## {i}. {demande}")
        if contexte:
            lignes.append(f"<!-- contexte : {len(contexte)} tour(s) precedent(s) -->")
        if not resultats:
            lignes.append("   (aucun skill propose)")
        for rang, r in enumerate(resultats, start=1):
            lignes.append(f"   {rang}. {r['name']}   [{r['category']}]  score {r['score']}")
        lignes.append(f"{MARQUEUR} ")
        lignes.append("")
    return "\n".join(lignes) + "\n"


_TITRE = re.compile(r"^##\s*(\d+)\.\s*(.*)$")
_CANDIDAT = re.compile(r"^\s*(\d+)\.\s+(\S+)\s")


def relire(texte: str) -> list[dict]:
    """Relit l'echantillon annote. Tolerant : une ligne mal remplie est signalee,
    pas fatale."""
    cas: list[dict] = []
    courant: dict | None = None
    for ligne in (texte or "").splitlines():
        titre = _TITRE.match(ligne)
        if titre:
            if courant is not None:
                cas.append(courant)
            courant = {
                "numero": int(titre.group(1)), "demande": titre.group(2),
                "candidats": [], "reponse": None, "brut": "",
            }
            continue
        if courant is None:
            continue
        candidat = _CANDIDAT.match(ligne)
        if candidat and not ligne.lstrip().startswith(MARQUEUR):
            courant["candidats"].append(candidat.group(2))
            continue
        if ligne.strip().startswith(MARQUEUR):
            brut = ligne.split(MARQUEUR, 1)[1].strip()
            courant["brut"] = brut
            if brut.isdigit():
                courant["reponse"] = int(brut)
    if courant is not None:
        cas.append(courant)
    return cas


def juger(cas: list[dict]) -> dict:
    """Le rappel reel, et la courbe du seuil sur des demandes reelles."""
    annotes = [c for c in cas if c["reponse"] is not None]
    aucun = [c for c in annotes if c["reponse"] == 0]
    designes = [c for c in annotes if c["reponse"] and c["reponse"] >= 1]
    total = len(annotes)
    if not total:
        return {
            "ok": False,
            "erreur": "aucune reponse annotee",
            "cas_lus": len(cas),
            "non_annotes": len(cas),
        }
    premier = sum(1 for c in designes if c["reponse"] == 1)
    dans_cinq = len(designes)
    return {
        "ok": True,
        "cas_lus": len(cas),
        "cas_annotes": total,
        "non_annotes": len(cas) - total,
        "aucun_ne_convient": len(aucun),
        # Les deux chiffres qu'on attendait depuis le debut, sur de vraies
        # demandes et non sur des phrases ecrites par les auteurs des skills.
        "rappel_1_reel": round(premier / total, 3),
        "rappel_5_reel": round(dans_cinq / total, 3),
        "part_sans_reponse_possible": round(len(aucun) / total, 3),
    }
