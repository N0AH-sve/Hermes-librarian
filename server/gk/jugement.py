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

**Le defaut de la version 1, et pourquoi il comptait.** La v1 n'offrait que deux
reponses : un rang parmi les cinq, ou `0`. L'annotateur n'avait donc aucun moyen
de dire « le bon skill existe, mais il n'est pas dans cette liste ». Trois
consequences, toutes mesurees sur le premier releve reel :

* `rappel_5` valait **mecaniquement** `1 - part_sans_reponse_possible`. Toute
  reponse autre que `0` designe un des cinq par construction, donc le chiffre
  ne portait aucune information sur la qualite de la recherche. 0,638 + 0,362 =
  1,000 exactement, et ce n'etait pas une coincidence ;
* `0` portait deux sens incompatibles -- « aucun skill de la bibliotheque ne
  sert cette demande » et « aucun de ces cinq-la » -- que la lecture imprimee
  confondait en annoncant un trou de la bibliotheque ;
* `rappel_1` restait valide, mais son denominateur devenait une question
  ouverte, donc un intervalle et non un point.

La v2 ajoute une troisieme reponse : **le nom du bon skill**, quand il existe
hors des cinq. Elle ne coute rien a l'annotateur -- un nom au lieu d'un chiffre
-- et elle resout les trois points d'un coup. `reprise` permet de n'annoter que
les cas `0` d'un echantillon v1 deja rempli, pour ne pas refaire l'heure.
"""

from __future__ import annotations

import re

from .recherche import Index, contexte_de_session
from .store import Store

MARQUEUR = "REPONSE:"
VERSION = 2
_MARQUE = "<!-- gk echantillon v"
# Un echantillon stratifie par longueur : les demandes courtes et longues ne
# posent pas le meme probleme, et un tirage simple sur-representerait les
# moyennes.
TRANCHES = ((0, 5), (6, 12), (13, 25), (26, 10_000))
# Profondeur de la seconde passe : les rangs 6 a 15 d'une demande dont
# l'annotateur a dit qu'aucun des cinq premiers ne convenait.
REPRISE_JUSQU_A = 15

_CONSIGNE = (
    "Pour chaque demande, ecrivez apres `REPONSE:` une seule chose :",
    "",
    "  * le NUMERO du bon skill, s'il est dans la liste ;",
    "  * le NOM du bon skill, s'il existe mais n'est PAS dans la liste",
    "    (`gk catalogue --noms` les liste tous) ;",
    "  * `0` si AUCUN skill de la bibliotheque ne sert cette demande ;",
    "  * `?` si vous ne savez pas.",
    "",
    "La distinction entre un nom et `0` est celle qui porte la mesure : sans",
    "elle, le rappel dans les cinq vaut mecaniquement l'inverse du taux de `0`",
    "et ne mesure plus rien.",
)


def _une_ligne(texte: str) -> str:
    """Une demande sur une seule ligne.

    Un message d'utilisateur peut contenir des retours a la ligne, et le format
    porte la demande dans un titre `## N. ...`. Sans ce repli, une demande
    multiligne casse la relecture -- et ses lignes suivantes peuvent meme etre
    prises pour des candidats.
    """
    return " ".join((texte or "").split())


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
        f"{_MARQUE}{VERSION} -->",
        "",
        *_CONSIGNE,
        "",
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
        demande = _une_ligne(row["text_truncated"])
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


def reprise(
    store: Store,
    index: Index,
    cas: list[dict],
    jusqu_a: int = REPRISE_JUSQU_A,
) -> str:
    """Seconde passe : les seuls cas que l'annotateur a classes `0`.

    Un echantillon v1 ne distingue pas « aucun skill ne sert cette demande » de
    « aucun de ces cinq-la ». La difference se recupere sans refaire l'heure :
    seuls les cas `0` la portent, et pour chacun il suffit de montrer **la
    suite** du classement.

    Les rangs affiches sont les rangs **reels**, donc une reponse `7` se lit
    comme un echec du rang 1 mais pas du rappel de la bibliotheque -- ce que la
    v1 ne pouvait pas exprimer.
    """
    a_revoir = [c for c in cas if c.get("reponse") == 0]
    lignes = [
        "# Reprise : les demandes classees « aucun ne convient »",
        f"{_MARQUE}{VERSION} -->",
        "",
        "Ces demandes seules restent ambigues : vous avez dit qu'aucun des cinq",
        "premiers ne convenait, et le format ne demandait pas si un autre skill",
        "aurait convenu. Voici la SUITE du classement pour chacune.",
        "",
        *_CONSIGNE,
        "",
        "Gardez `0` si vraiment aucun skill de la bibliotheque ne sert la",
        "demande : c'est une reponse, et c'est meme la plus interessante.",
        "",
        "Ce fichier se relit AVEC le premier, et il le corrige :",
        "    cat echantillon.txt reprise.txt | gk juger",
        "",
        "---",
        "",
    ]
    if not a_revoir:
        lignes.append("(aucun cas classe `0` : rien a reprendre)")
        return "\n".join(lignes) + "\n"

    # On retrouve la session pour reproduire le classement d'origine : sans le
    # contexte, la suite affichee ne serait pas la suite du meme classement.
    rows = store.query(
        "SELECT session_id, text_truncated FROM requests "
        "WHERE text_truncated IS NOT NULL AND text_truncated <> '' "
        "ORDER BY ts DESC LIMIT 1000"
    )
    sessions = {_une_ligne(r["text_truncated"]): (r["session_id"] or "") for r in rows}

    for c in a_revoir:
        demande = _une_ligne(c.get("demande") or "")
        session = sessions.get(demande, "")
        contexte = contexte_de_session(store, session) if session else []
        if contexte:
            resultats = index.chercher_avec_contexte(
                demande, contexte=contexte, limite=jusqu_a
            )
        else:
            resultats = index.chercher(demande, limite=jusqu_a)
        deja = len(c.get("candidats") or [])
        suite = resultats[deja:]
        lignes.append(f"## {c['numero']}. {demande}")
        lignes.append(f"<!-- v1 : les rangs 1 a {deja} ont deja ete ecartes -->")
        if not suite:
            lignes.append(
                f"   (le classement s'arrete au rang {len(resultats)} : "
                "aucun autre candidat)"
            )
        for rang, r in enumerate(suite, start=deja + 1):
            lignes.append(f"   {rang}. {r['name']}   [{r['category']}]  score {r['score']}")
        lignes.append(f"{MARQUEUR} ")
        lignes.append("")
    return "\n".join(lignes) + "\n"


_TITRE = re.compile(r"^##\s*(\d+)\.\s*(.*)$")
_CANDIDAT = re.compile(r"^\s*(\d+)\.\s+(\S+)\s")
_VERSION = re.compile(re.escape(_MARQUE) + r"(\d+)")
_NOM = re.compile(r"^[A-Za-z][A-Za-z0-9._/-]{2,}$")


def relire(texte: str) -> list[dict]:
    """Relit l'echantillon annote. Tolerant : une ligne mal remplie est signalee,
    pas fatale.

    Deux fichiers concatenes se relisent ensemble, et **le dernier gagne** :
    c'est ce qui permet a `gk reprise` de corriger les cas ambigus du premier
    sans refaire l'annotation entiere.
    """
    version = 1
    marque = _VERSION.search(texte or "")
    if marque:
        version = int(marque.group(1))

    cas: list[dict] = []
    courant: dict | None = None
    for ligne in (texte or "").splitlines():
        titre = _TITRE.match(ligne)
        if titre:
            if courant is not None:
                cas.append(courant)
            courant = {
                "numero": int(titre.group(1)), "demande": titre.group(2),
                "candidats": [], "reponse": None, "nom": None, "brut": "",
                "version": version,
            }
            continue
        if courant is None:
            continue
        candidat = _CANDIDAT.match(ligne)
        if candidat and not ligne.lstrip().startswith(MARQUEUR):
            courant["candidats"].append(candidat.group(2))
            # Le rang affiche est le rang reel : une reprise commence a 6.
            courant.setdefault("premier_rang", int(candidat.group(1)))
            continue
        if ligne.strip().startswith(MARQUEUR):
            brut = ligne.split(MARQUEUR, 1)[1].strip()
            courant["brut"] = brut
            if brut.isdigit():
                courant["reponse"] = int(brut)
            elif _NOM.match(brut):
                # Le bon skill existe, hors de la liste montree. Si c'est un des
                # candidats affiches, on le ramene a son rang.
                courant["nom"] = brut
                rangs = courant.get("candidats") or []
                depart = courant.get("premier_rang", 1)
                for i, nom in enumerate(rangs):
                    if nom == brut:
                        courant["reponse"] = depart + i
                        courant["nom"] = None
                        break
    if courant is not None:
        cas.append(courant)

    # Le dernier cas portant une reponse l'emporte, par numero.
    fusion: dict[int, dict] = {}
    for c in cas:
        n = c["numero"]
        if n not in fusion:
            fusion[n] = c
        elif c["reponse"] is not None or c["nom"]:
            fusion[n] = c
    return [fusion[n] for n in sorted(fusion)]


def juger(cas: list[dict], dans_les_n: int = 5) -> dict:
    """Le rappel reel, sur le seul denominateur qui ait un sens.

    **Le denominateur est le nombre de demandes qu'un skill de la bibliotheque
    peut servir.** Une demande qu'aucun skill ne sert n'est pas un echec de
    recherche, et la compter en abaisserait le chiffre sans rien apprendre.

    Quand l'echantillon est en v1, ce denominateur n'est pas connu : les `0`
    melangent les trous de la bibliotheque et les skills simplement manques.
    Le resultat est alors rendu **en intervalle**, et le point n'est pas
    annonce -- un chiffre faux est plus couteux qu'un chiffre absent.
    """
    annotes = [c for c in cas if c.get("reponse") is not None or c.get("nom")]
    total = len(annotes)
    if not total:
        return {
            "ok": False,
            "erreur": "aucune reponse annotee",
            "cas_lus": len(cas),
            "non_annotes": len(cas),
        }

    version = max((c.get("version") or 1) for c in annotes)
    rangs = [c["reponse"] for c in annotes if c.get("reponse")]
    premier = sum(1 for r in rangs if r == 1)
    dans_cinq = sum(1 for r in rangs if r <= dans_les_n)
    hors_cinq = sum(1 for r in rangs if r > dans_les_n)
    hors_cinq += sum(1 for c in annotes if c.get("nom"))
    aucun = sum(1 for c in annotes if c.get("reponse") == 0)

    # Le rang auquel le bon skill se trouve QUAND il est cite : c'est lui qui
    # separe un probleme de classement d'un probleme de recherche. Le bon skill
    # au rang 2 se corrige par des coefficients ; au rang 5, a peine.
    distribution = {}
    for r in sorted(set(rangs)):
        distribution[str(r)] = sum(1 for x in rangs if x == r)

    out = {
        "ok": True,
        "cas_lus": len(cas),
        "cas_annotes": total,
        "non_annotes": len(cas) - total,
        "version_echantillon": version,
        f"designe_dans_les_{dans_les_n}": dans_cinq,
        "designe_au_rang_1": premier,
        f"designe_hors_des_{dans_les_n}": hors_cinq,
        "aucun_skill_ne_convient": aucun,
        "distribution_des_rangs": distribution,
        # Le taux de CITATION sur toutes les demandes annotees. Celui-la ne
        # depend d'aucune hypothese, et c'est le denominateur que voit un brief :
        # un brief ne sait pas si une demande a une reponse.
        f"cite_dans_les_{dans_les_n}_sur_tout": round(dans_cinq / total, 3),
        "part_premier_quand_cite": (
            round(premier / dans_cinq, 3) if dans_cinq else None
        ),
    }

    servables = dans_cinq + hors_cinq
    if version >= 2:
        # `0` veut dire ce qu'il dit : le denominateur est connu.
        out["demandes_servables"] = servables
        out["part_sans_reponse_possible"] = round(aucun / total, 3)
        if servables:
            out["rappel_1_reel"] = round(premier / servables, 3)
            out[f"rappel_{dans_les_n}_reel"] = round(dans_cinq / servables, 3)
        else:
            out["rappel_1_reel"] = None
            out[f"rappel_{dans_les_n}_reel"] = None
            out["avertissement"] = (
                "aucune demande annotee n'a de reponse dans la bibliotheque : "
                "rien a mesurer"
            )
        return out

    # v1 : les `0` sont ambigus, donc le denominateur est un intervalle.
    out["ambigus"] = aucun
    out["rappel_1_borne_basse"] = round(premier / total, 3)
    out["rappel_1_borne_haute"] = (
        round(premier / servables, 3) if servables else None
    )
    out[f"rappel_{dans_les_n}_borne_basse"] = round(dans_cinq / total, 3)
    out[f"rappel_{dans_les_n}_borne_haute"] = (
        round(dans_cinq / servables, 3) if servables else None
    )
    out["avertissement"] = (
        f"echantillon v1 : les {aucun} reponses `0` ne distinguent pas « aucun "
        "skill ne sert cette demande » de « aucun de ces cinq-la ». Le rappel "
        f"dans les {dans_les_n} y vaut mecaniquement l'inverse du taux de `0` "
        "et ne mesure rien. `gk reprise` leve l'ambiguite sans refaire "
        "l'annotation entiere."
    )
    return out
