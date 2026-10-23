"""La recherche : cinq champs, des poids, et rien de plus.

Ce qui a rendu cette etape petite, c'est une suite de relevés qui ont supprime
du travail au lieu d'en ajouter :

* le prompt systeme de Hermes porte **deja** le catalogue complet, donc GK ne
  cherche pas a informer gb de ce qui existe. Il **designe**, il ne liste pas ;
* seuls quatre champs sont indexes, puis cinq avec les exemples. Moins de 120 Ko
  pour 183 skills : **aucun FTS5, aucun BM25 a regler, aucun corps a
  transferer** ;
* et les declencheurs sont redigees comme des **citations de l'utilisateur** --
  296 lignes entre guillemets. Le texte indexe a donc la meme forme que les
  demandes, ce qui est la condition pour que le lexical suffise.

**L'index se construit a la volee**, a chaque requete, depuis les tranches
brutes deja en base. 183 skills et 120 Ko : le decoupage est ligne a ligne et le
score une somme ponderee, donc cela se compte en millisecondes. Ne rien
materialiser supprime d'un coup toute une classe de defauts -- un index perime,
une regle changee sans relecture, une migration -- et permet de modifier les
poids sans rien reconstruire. Si cela devient lent un jour, on materialisera, et
on le saura par la mesure plutot que par precaution.

Deux choix de score meritent d'etre dits :

* **l'IDF est indispensable ici.** `prestashop` apparait dans beaucoup de
  skills, `proforma` dans un seul. Les compter pareil ferait remonter le mauvais
  skill sur toute demande qui nomme un outil courant ;
* **la presence, pas la frequence.** Un champ long gagnerait mecaniquement s'il
  suffisait de repeter un mot. Un jeton present dans un champ compte une fois.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .declencheurs import GUILLEMETS, decouper
from .patterns import fold
from .store import Store

# Les cinq champs, et leurs poids par defaut. Chacun porte une justification
# mesuree, pas une intuition.
POIDS: dict[str, float] = {
    # 296 lignes entre guillemets sur 87 skills : du texte de la meme forme que
    # les demandes. Le signal le plus fort disponible.
    "exemples": 3.0,
    # Court, unique, et c'est ce que l'agent doit citer.
    "name": 2.5,
    # Presente sur 183 skills sur 183, et redigee avec du vocabulaire reel.
    "description": 2.0,
    # Le reste de la prose des declencheurs : utile mais verbeux, 95 Ko.
    "positif": 1.0,
}

# Poids de la soustraction. 31 skills sur 142 portent une contre-indication :
# marginal en nombre, mais c'est ce qui empeche l'inversion sur les cas les plus
# nets -- une ligne « ne pas charger pour une commande client » ferait
# autrement remonter le skill en tete sur « commande client ».
LAMBDA_NEGATIF = 2.0

# La categorie qui porte le metier. Relevé du 2026-10-22 : 86 skills sur 183,
# et c'est la population ou les demandes des utilisateurs tombent. Sert a
# decouper les resultats du banc, JAMAIS a ponderer : voir `chercher`.
CATEGORIE_METIER = "greenmusic"

# Mots trop communs pour discriminer. Les deux langues, la bibliotheque etant
# bilingue. Volontairement courte : l'IDF fait le reste du travail, et une liste
# longue finit par retirer un mot du metier.
STOPWORDS = frozenset(
    """
    le la les un une des du de au aux et ou mais donc or ni car que qui quoi
    dont ou a en y il elle ils elles on nous vous je tu me te se ce cet cette
    ces mon ma mes ton ta tes son sa ses leur leurs pour par sur sous dans avec
    sans chez vers entre est sont etre ete fait faire si plus moins tres tout
    tous toute toutes meme aussi comme quand
    the a an and or but so nor for of to in on at by with from as is are be
    been being this that these those it its he she they we you i my your their
    not no if then than there here what which who whom whose how when while
    """.split()
)

# Poids des jetons venus des tours precedents. Ils orientent, ils ne decident
# pas : au poids plein, la conversation ecraserait la question.
#
# ACTIVE par defaut depuis le relevé du 2026-10-23, qui est le meilleur resultat
# du projet : +0,246 de MRR quand la conversation reste sur le sujet, -0,029
# quand elle change. Le seuil de rentabilite est donc a
# 0,029 / (0,246 + 0,029) = **11 %** de tours restant sur le sujet. Il faudrait
# que 89 % des tours changent de sujet pour que le contexte nuise.
POIDS_CONTEXTE = 0.3

# Combien de tours precedents sont repris, et sur quelle duree. Court a dessein :
# une conversation de deux heures n'oriente plus la question presente.
CONTEXTE_TOURS = 3
CONTEXTE_FENETRE_SECONDES = 1800.0

MIN_JETON = 2        # « PA », « IV », « PRE » sont des termes metier
MAX_RESULTATS = 50


def jetons(texte: str) -> list[str]:
    """Decoupe en jetons, plie les accents, retire les mots vides.

    Pas de racinisation. Le relevé du 2026-10-22 montre une bibliotheque
    bilingue, donc un stemmer francais seul serait un mauvais choix ; et comme
    l'index se reconstruit a chaque requete, l'ajouter plus tard ne coutera
    aucune relecture. On commence sans, et le banc dira s'il manque.
    """
    return [
        j for j in re.split(r"[^0-9a-z]+", fold(texte or ""))
        if len(j) >= MIN_JETON and j not in STOPWORDS
    ]


@dataclass
class Skill:
    path: str
    name: str
    category: str
    champs: dict[str, set[str]]       # champ -> jetons presents
    negatif: set[str] = field(default_factory=set)
    exemples_brut: list[str] = field(default_factory=list)

    def a_des_declencheurs(self) -> bool:
        return bool(self.champs.get("exemples") or self.champs.get("positif"))


def lignes_citees_de(texte: str) -> list[str]:
    return [
        ligne for ligne in (texte or "").splitlines()
        if any(g in ligne for g in GUILLEMETS)
    ]


def contexte_de_session(
    store: Store,
    session_id: str,
    tours: int = CONTEXTE_TOURS,
    fenetre: float = CONTEXTE_FENETRE_SECONDES,
    avant: float | None = None,
) -> list[str]:
    """Les demandes precedentes d'une session, les plus recentes d'abord.

    Lues depuis les demandes reelles deja enregistrees : rien de nouveau n'est
    collecte pour cela. La fenetre est courte parce qu'une conversation de deux
    heures n'oriente plus la question presente.

    **`avant` est obligatoire pour toute evaluation retrospective, et c'est un
    defaut corrige.** Sans lui, la fonction s'ancre sur `MAX(ts)` de la session
    : correct en direct, puisque la demande courante y est la plus recente, mais
    **faux pour une demande du passe**, ou elle renvoie les derniers tours de la
    conversation -- donc le futur de la demande jugee.

    Releve par gb le 2026-10-23 : applique a l'historique, l'ancrage sur
    `MAX(ts)` changeait 76 classements sur 104 et 23 rangs 1. Tout ce qui a ete
    mesure a travers `gk echantillon` avant cette correction est gonfle.

    En passant `avant`, seules les demandes **strictement anterieures** sont
    lues, et la demande jugee n'a plus besoin d'etre retiree de la liste.
    """
    if not session_id:
        return []
    if avant is not None:
        rows = store.query(
            "SELECT text_truncated FROM requests WHERE session_id = ? "
            "AND ts < ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
            (session_id, avant, avant - fenetre, max(1, tours)),
        )
        return [r["text_truncated"] for r in rows if r["text_truncated"]]
    rows = store.query(
        "SELECT text_truncated FROM requests WHERE session_id = ? "
        "AND ts >= (SELECT MAX(ts) FROM requests WHERE session_id = ?) - ? "
        "ORDER BY ts DESC LIMIT ?",
        (session_id, session_id, fenetre, max(1, tours) + 1),
    )
    textes = [r["text_truncated"] for r in rows if r["text_truncated"]]
    # La plus recente est la demande courante : elle n'est pas son propre contexte.
    return textes[1:] if len(textes) > 1 else []


@dataclass
class Index:
    skills: list[Skill]
    df: dict[str, int]                # nombre de skills portant le jeton
    n: int
    # Nombre moyen de jetons distincts par champ. Sert a normaliser par la
    # longueur : un champ long rencontre plus de demandes par hasard.
    longueur_moyenne: dict[str, float] = field(default_factory=dict)

    @classmethod
    def construire(cls, store: Store) -> "Index":
        """Construit l'index en memoire depuis les tranches brutes.

        Les tranches sont gardees brutes en base precisement pour cela :
        changer le decoupage ou les poids ne demande pas de redemander les
        fichiers a l'agent.
        """
        sections: dict[str, list[tuple[str, str]]] = {}
        for row in store.query(
            "SELECT path, heading, text FROM skill_sections ORDER BY path, kind"
        ):
            sections.setdefault(row["path"], []).append(
                (row["heading"] or "", row["text"] or "")
            )

        skills: list[Skill] = []
        df: dict[str, int] = {}
        for row in store.query(
            "SELECT path, name, description, category FROM skills "
            "WHERE missing_since IS NULL ORDER BY path"
        ):
            exemples: list[str] = []
            positif: list[str] = []
            negatif: list[str] = []
            for heading, texte in sections.get(row["path"], []):
                pos, neg, _ = decouper(heading, texte)
                # Le titre compte : GM ecrit `## Declencheur : "phrase"`, donc
                # la phrase peut y etre.
                citees = lignes_citees_de(heading + "\n" + pos)
                exemples.extend(citees)
                reste = [l for l in pos.splitlines() if l not in citees]
                positif.append(heading)
                positif.extend(reste)
                negatif.append(neg)
            champs = {
                "name": set(jetons(row["name"])),
                "description": set(jetons(row["description"])),
                "exemples": set(jetons("\n".join(exemples))),
                "positif": set(jetons("\n".join(positif))),
            }
            skill = Skill(
                path=row["path"], name=row["name"], category=row["category"],
                champs=champs, negatif=set(jetons("\n".join(negatif))),
                exemples_brut=exemples,
            )
            skills.append(skill)
            vus: set[str] = set()
            for jetons_champ in champs.values():
                vus |= jetons_champ
            for j in vus:
                df[j] = df.get(j, 0) + 1
        longueurs: dict[str, list[int]] = {}
        for s in skills:
            for champ, jetons_champ in s.champs.items():
                longueurs.setdefault(champ, []).append(len(jetons_champ))
        moyennes = {
            champ: (sum(valeurs) / len(valeurs)) if valeurs else 0.0
            for champ, valeurs in longueurs.items()
        }
        return cls(skills=skills, df=df, n=len(skills), longueur_moyenne=moyennes)

    def facteur_longueur(self, champ: str, nombre_de_jetons: int) -> float:
        """Corrige l'avantage d'un champ long.

        Relevé du 2026-10-22 : pour les skills generiques, nom + description
        seuls battent toute combinaison essayee. Ni la prose ni les declencheurs
        n'en sont la cause -- c'est **le fait d'ajouter du texte**. Un champ qui
        porte plus de jetons distincts rencontre plus de demandes par hasard,
        meme sur des mots peu discriminants, et les skills generiques herites
        sont longs et verbeux la ou les descriptions metier sont courtes.

        C'est le biais que BM25 corrige par la longueur moyenne de document, et
        c'est precisement la partie de BM25 qui avait ete jugee inutile ici. La
        normalisation est rapportee a la moyenne du champ, pour que les poids
        gardent leur sens.
        """
        moyenne = self.longueur_moyenne.get(champ, 0.0)
        if moyenne <= 0 or nombre_de_jetons <= 0:
            return 1.0
        return math.log(2.0 + moyenne) / math.log(2.0 + nombre_de_jetons)

    def idf(self, jeton: str) -> float:
        """Un jeton present partout ne discrimine rien.

        `prestashop` apparait dans beaucoup de skills, `proforma` dans un seul.
        Sans ce facteur, toute demande nommant un outil courant remonterait le
        mauvais skill.
        """
        return math.log(1.0 + self.n / (1.0 + self.df.get(jeton, 0)))

    def chercher(
        self,
        demande: str,
        limite: int = 10,
        poids: dict[str, float] | None = None,
        lambda_negatif: float = LAMBDA_NEGATIF,
        sans_declencheurs: bool = False,
        exclure_exemple: str | None = None,
        sans_prose: bool = False,
        normaliser_longueur: bool = False,
    ) -> list[dict]:
        """Classe les skills pour une demande.

        `sans_declencheurs` ne garde que le nom et la description : c'est le bras
        temoin du banc, et il repond a la question de GM — « le
        bibliothécaire a-t-il besoin de plus que nom + description ? »

        `exclure_exemple` retire une ligne citee de l'index du skill qui la
        porte. Sans cela, mesurer sur les 296 lignes citees serait **circulaire** :
        un skill retrouverait trivialement sa propre citation.

        `sans_prose` garde les citations et jette le reste du texte des
        declencheurs. Les citations sont des phrases d'utilisateur, propres ;
        la prose est verbeuse et c'est **la que vivent les renvois vers les
        skills voisins**, qui font remonter le mauvais skill.

        Une option **retiree** apres mesure, et la raison vaut d'etre gardee :
        ponderer PAR POPULATION -- indexer les declencheurs du metier seulement
        -- rend les scores incomparables. Un skill note sur quatre champs bat
        mecaniquement un skill note sur deux, donc la regle avantageait le metier
        au lieu d'ameliorer la recherche. Tous les skills doivent etre notes sur
        les memes champs avec les memes poids.
        """
        p = dict(poids or POIDS)
        if sans_declencheurs:
            p = {c: v for c, v in p.items() if c in ("name", "description")}
        elif sans_prose:
            p = {c: v for c, v in p.items() if c != "positif"}
        jetons_demande = jetons(demande)
        if not jetons_demande:
            return []
        resultats: list[dict] = []
        for skill in self.skills:
            detail: dict[str, float] = {}
            total = 0.0
            for champ, poids_champ in p.items():
                presents = skill.champs.get(champ) or set()
                if champ == "exemples" and exclure_exemple is not None:
                    presents = self._exemples_sans(skill, exclure_exemple)
                gagne = sum(
                    self.idf(j) for j in jetons_demande if j in presents
                )
                if gagne and normaliser_longueur:
                    gagne *= self.facteur_longueur(champ, len(presents))
                if gagne:
                    detail[champ] = round(poids_champ * gagne, 3)
                    total += poids_champ * gagne
            if not sans_declencheurs and skill.negatif:
                perdu = sum(
                    self.idf(j) for j in jetons_demande if j in skill.negatif
                )
                if perdu:
                    detail["negatif"] = -round(lambda_negatif * perdu, 3)
                    total -= lambda_negatif * perdu
            if total > 0:
                resultats.append({
                    "name": skill.name,
                    "path": skill.path,
                    "category": skill.category,
                    "score": round(total, 3),
                    "detail": detail,
                })
        resultats.sort(key=lambda r: (-r["score"], r["name"]))
        return resultats[: max(1, min(limite, MAX_RESULTATS))]

    def _exemples_sans(self, skill: Skill, ligne: str) -> set[str]:
        """Les jetons des exemples du skill, la ligne donnee retiree."""
        gardees = [l for l in skill.exemples_brut if l != ligne]
        if len(gardees) == len(skill.exemples_brut):
            return skill.champs.get("exemples") or set()
        return set(jetons("\n".join(gardees)))

    # -- diagnostic d'un echec ----------------------------------------------

    def jetons_apparies(
        self, skill: Skill, jetons_demande: list[str], exclure_exemple: str | None = None
    ) -> set[str]:
        """Les jetons de la demande que ce skill porte, tous champs confondus."""
        porte: set[str] = set()
        for champ, presents in skill.champs.items():
            if champ == "exemples" and exclure_exemple is not None:
                presents = self._exemples_sans(skill, exclure_exemple)
            porte |= presents
        return {j for j in jetons_demande if j in porte}

    def diagnostic(
        self, demande: str, attendu: str, exclure_exemple: str | None = None,
        limite: int = 5,
    ) -> dict:
        """Pourquoi un cas echoue, et **quel remede** s'applique.

        "88 echecs de classement" est un tas indifferencie, et les remedes sont
        opposes. Trois causes, dans l'ordre ou elles s'excluent :

        * `texte_absent` — le skill attendu ne marque **aucun point**. Les mots
          de la demande n'existent nulle part dans son texte : c'est de la
          REDACTION, et aucun coefficient n'y changera rien ;
        * `texte_domine` — le gagnant porte **tous** les jetons que porte
          l'attendu, et au moins un de plus. Aucune ponderation ne peut inverser
          cela sans casser tout le reste : REDACTION encore ;
        * `ordre` — les deux portent des jetons differents, mais le classement
          se trompe. C'est la seule cause que le SCORE peut corriger.

        Ce decoupage borne la valeur du reglage : si la majorite des echecs sont
        du texte, regler les coefficients ne sert presque a rien.
        """
        jetons_demande = jetons(demande)
        resultats = self.chercher(
            demande, limite=max(limite, 10), exclure_exemple=exclure_exemple
        )
        noms = [r["name"] for r in resultats]
        rang = noms.index(attendu) + 1 if attendu in noms else None
        if rang is not None and rang <= limite:
            return {"cause": None, "rang": rang}
        par_nom = {s.name: s for s in self.skills}
        cible = par_nom.get(attendu)
        if cible is None:
            return {"cause": "skill_inconnu", "rang": None}
        porte_cible = self.jetons_apparies(cible, jetons_demande, exclure_exemple)
        if not porte_cible:
            return {
                "cause": "texte_absent", "remede": "redaction", "rang": rang,
                "jetons_demande": len(jetons_demande), "jetons_portes": 0,
            }
        gagnant = par_nom.get(noms[0]) if noms else None
        if gagnant is not None:
            porte_gagnant = self.jetons_apparies(gagnant, jetons_demande, exclure_exemple)
            if porte_cible < porte_gagnant:
                return {
                    "cause": "texte_domine", "remede": "redaction", "rang": rang,
                    "gagnant": gagnant.name,
                    "jetons_portes": len(porte_cible),
                    "jetons_du_gagnant": len(porte_gagnant),
                }
        return {
            "cause": "ordre", "remede": "score", "rang": rang,
            "gagnant": noms[0] if noms else None,
            "jetons_portes": len(porte_cible),
        }

    # -- la confiance --------------------------------------------------------

    def confiance(self, demande: str, resultats: list[dict]) -> dict:
        """Les signaux qui disent si le premier resultat est un gagnant clair.

        Etape 4 de la liste de GM : verifier la coherence avant de proposer. GK
        rendait toujours une liste, donc sa precision etait condamnee a etre son
        rappel -- 51 % sur le metier. Pouvoir se taire separe les deux : il ne
        parle que la ou il est fort.

        Aucun de ces signaux n'est combine ici en une formule. `gk seuil`
        mesure leur courbe precision/couverture et garde celui qui donne la
        meilleure couverture a la precision visee -- plutot qu'une formule
        inventee puis defendue.
        """
        jetons_demande = jetons(demande)
        if not resultats or not jetons_demande:
            return {
                "marge": 0.0, "couverture": 0.0, "score": 0.0,
                "information": round(self.information(demande), 3),
            }
        premier = resultats[0]
        second = resultats[1]["score"] if len(resultats) > 1 else 0.0
        s1 = premier["score"] or 0.0
        par_nom = {s.name: s for s in self.skills}
        gagnant = par_nom.get(premier["name"])
        portes = (
            self.jetons_apparies(gagnant, jetons_demande) if gagnant is not None
            else set()
        )
        return {
            # Insensible a la longueur de la demande, contrairement au score.
            "marge": round((s1 - second) / s1, 3) if s1 > 0 else 0.0,
            "couverture": round(len(portes) / len(jetons_demande), 3),
            "score": round(s1, 3),
            "information": round(self.information(demande), 3),
        }

    # -- paires que rien ne separe -------------------------------------------

    def doublons_de_nom(self) -> list[dict]:
        """Les skills qui portent le MEME nom a des chemins differents.

        Relevé du 2026-10-23 : `gk confusions` a rendu des paires d'un skill avec
        lui-meme, proximite 1,0 -- `dogfood` et `codebase-inspection`. Ce
        n'etait pas un defaut du rapport. Les journaux de gb le disaient deja :
        « 4 user-modified (kept): codebase-inspection, dogfood, powerpoint,
        computer-use ». Hermes livre une version groupee, la version locale est
        gardee, et les deux coexistent dans l'arborescence.

        `prompt_builder` deduplique par nom, donc gb n'en voit qu'un. GK indexe
        les deux : ils se concurrencent et se partagent l'IDF, ce qui abaisse
        leur score a tous les deux.
        """
        par_nom: dict[str, list[str]] = {}
        for skill in self.skills:
            if skill.name:
                par_nom.setdefault(skill.name, []).append(skill.path)
        return [
            {"name": nom, "chemins": sorted(chemins)}
            for nom, chemins in sorted(par_nom.items())
            if len(chemins) > 1
        ]

    def manques(self, cas: list[tuple[str, str, str]]) -> list[dict]:
        """La liste de ce qu'il faut ECRIRE, groupee par skill.

        67 % des echecs du banc relevent de la redaction, donc le travail utile
        n'est pas un reglage de coefficients : c'est une liste de descriptions a
        enrichir. Pour chaque skill, les demandes qu'il devrait capter et les
        **mots qui manquent a son texte** -- pas une statistique, une liste de
        courses.
        """
        par_nom = {s.name: s for s in self.skills}
        groupes: dict[str, dict] = {}
        # Un mot qui manque a BEAUCOUP de skills differents n'est pas un mot de
        # domaine, c'est un mot vide que la liste des mots vides n'a pas attrape.
        #
        # Releve du 2026-10-23 : la premiere version de ce rapport conseillait
        # d'ajouter « veux », « ne », « pas », « fais », « moi » et des
        # nombres comme « 532 ». La liste de mots vides avait ete gardee courte
        # a dessein -- « l'IDF fait le reste » -- ce qui est vrai pour le SCORE
        # et faux pour une liste de courses, puisqu'un mot absent de toute la
        # bibliotheque a l'IDF maximal et parait donc maximalement precieux.
        manquants_par_mot: dict[str, set[str]] = {}
        for demande, attendu, ligne in cas:
            cible = par_nom.get(attendu)
            if cible is None:
                continue
            portes = self.jetons_apparies(cible, jetons(demande), exclure_exemple=ligne)
            for j in jetons(demande):
                if j not in portes:
                    manquants_par_mot.setdefault(j, set()).add(attendu)
        trop_repandus = {
            mot for mot, skills in manquants_par_mot.items() if len(skills) >= 4
        }
        for demande, attendu, ligne in cas:
            cible = par_nom.get(attendu)
            if cible is None:
                continue
            jetons_demande = jetons(demande)
            if not jetons_demande:
                continue
            portes = self.jetons_apparies(cible, jetons_demande, exclure_exemple=ligne)
            manquants = [
                j for j in jetons_demande
                if j not in portes
                and j not in trop_repandus       # mot vide deguise
                and not j.isdigit()              # un nombre ne se redige pas
                and len(j) >= 3                  # ni un fragment
            ]
            if not manquants:
                continue
            groupe = groupes.setdefault(
                attendu,
                {"name": attendu, "category": cible.category, "cas": 0,
                 "mots_manquants": {}, "exemples": []},
            )
            groupe["cas"] += 1
            for j in manquants:
                groupe["mots_manquants"][j] = groupe["mots_manquants"].get(j, 0) + 1
            if len(groupe["exemples"]) < 3:
                groupe["exemples"].append(demande)
        sortie = []
        for groupe in groupes.values():
            mots = sorted(
                groupe["mots_manquants"].items(), key=lambda kv: (-kv[1], kv[0])
            )
            groupe["mots_manquants"] = [m for m, _ in mots[:12]]
            sortie.append(groupe)
        sortie.sort(key=lambda g: (-g["cas"], g["name"]))
        return sortie

    # -- le contexte de session ----------------------------------------------

    def chercher_avec_contexte(
        self,
        demande: str,
        contexte: list[str] | None = None,
        poids_contexte: float = POIDS_CONTEXTE,
        **kwargs,
    ) -> list[dict]:
        """Classe les skills en tenant compte des demandes precedentes.

        C'est l'etape 1 de la liste de GM -- comprendre l'intention et le
        contexte -- et la seule facon d'attaquer les demandes qui ne portent
        aucun mot de domaine. « etape 1 validee, go » est introuvable seule et
        parfaitement resoluble si le tour precedent parlait de l'index.

        Les jetons du contexte comptent moins que ceux de la demande : ils
        orientent, ils ne decident pas. Un contexte au poids plein ferait que
        la conversation ecrase la question.
        """
        # `limite` est retiree des kwargs : les deux recherches internes se
        # font large, et c'est le melange qui coupe.
        limite = int(kwargs.pop("limite", 10))
        jetons_demande = set(jetons(demande))
        jetons_contexte = set()
        for precedente in contexte or []:
            jetons_contexte |= set(jetons(precedente))
        jetons_contexte -= jetons_demande
        if not jetons_contexte or poids_contexte <= 0:
            return self.chercher(demande, limite=limite, **kwargs)
        # Le contexte est ajoute a la demande, et sa contribution est reduite en
        # repondant deux fois : une fois sur la demande, une fois sur le
        # contexte, puis on melange les scores.
        principaux = {r["name"]: r for r in self.chercher(demande, limite=MAX_RESULTATS, **kwargs)}
        appoints = {
            r["name"]: r for r in self.chercher(
                " ".join(sorted(jetons_contexte)), limite=MAX_RESULTATS, **kwargs
            )
        }
        melange: dict[str, dict] = {}
        for nom in set(principaux) | set(appoints):
            base = principaux.get(nom)
            appoint = appoints.get(nom)
            score = (base["score"] if base else 0.0) + poids_contexte * (
                appoint["score"] if appoint else 0.0
            )
            if score <= 0:
                continue
            modele = base or appoint
            detail = dict((base or {}).get("detail") or {})
            if appoint:
                detail["contexte"] = round(poids_contexte * appoint["score"], 3)
            melange[nom] = {**modele, "score": round(score, 3), "detail": detail}
        resultats = sorted(melange.values(), key=lambda r: (-r["score"], r["name"]))
        return resultats[: max(1, min(limite, MAX_RESULTATS))]

    def corpus_avec_contexte(self) -> list[dict]:
        """Un corpus de sessions **synthetiques**, pour mesurer le contexte.

        Le corpus curate est fait de phrases isolees : il n'y a pas de
        conversation autour d'elles, donc le contexte ne pouvait pas etre
        mesure. On en fabrique une : pour un skill portant au moins deux
        citations, l'une sert de tour precedent et l'autre de demande.

        Deux conditions, et la seconde importe autant que la premiere :

        * **suite** -- le tour precedent porte sur le MEME skill. Le contexte
          devrait aider ;
        * **bascule** -- le tour precedent porte sur un AUTRE skill. Le contexte
          devrait nuire, et il faut savoir combien.

        Non circulaire, puisque la phrase servant de contexte est differente de
        celle qu'on cherche, et que celle-ci est retiree de l'index comme
        ailleurs.
        """
        par_skill: dict[str, list[tuple[str, str]]] = {}
        for demande, attendu, ligne in self.corpus():
            par_skill.setdefault(attendu, []).append((demande, ligne))
        noms = sorted(par_skill)
        cas: list[dict] = []
        for i, nom in enumerate(noms):
            phrases = par_skill[nom]
            if len(phrases) < 2:
                continue
            (precedente, _), (demande, ligne) = phrases[0], phrases[1]
            cas.append({
                "condition": "suite", "demande": demande, "attendu": nom,
                "ligne": ligne, "contexte": [precedente],
            })
            # La bascule : le contexte vient du skill suivant dans l'ordre.
            autre = noms[(i + 1) % len(noms)]
            if autre != nom and par_skill.get(autre):
                cas.append({
                    "condition": "bascule", "demande": demande, "attendu": nom,
                    "ligne": ligne, "contexte": [par_skill[autre][0][0]],
                })
        return cas

    def confusions(self, seuil: float = 0.6) -> list[dict]:
        """Les paires de skills dont les textes sont trop proches pour être
        départagés.

        Ce n'est pas un défaut de GK : si deux textes ne se distinguent pas,
        aucun index ne les distinguera. C'est un défaut de la bibliothèque, et
        GK est bien placé pour le signaler.

        La moitié négative des déclencheurs **est** le mécanisme de
        désambiguïsation prévu par la conception. Une paire confuse dont aucun
        des deux ne porte d'interdiction est donc une paire où le mécanisme
        existe et n'a pas été utilisé : c'est là que l'écriture rapporte le plus.
        """
        sacs = []
        for skill in self.skills:
            sac: set[str] = set()
            for presents in skill.champs.values():
                sac |= presents
            if len(sac) >= 3:
                sacs.append((skill, sac))
        paires: list[dict] = []
        for i, (a, sac_a) in enumerate(sacs):
            for b, sac_b in sacs[i + 1:]:
                # Un meme nom a deux chemins n'est pas une paire confuse, c'est
                # un doublon : il a son propre rapport, et le melanger ici
                # masquait les vraies paires.
                if a.name and a.name == b.name:
                    continue
                commun = len(sac_a & sac_b)
                if not commun:
                    continue
                jaccard = commun / len(sac_a | sac_b)
                if jaccard < seuil:
                    continue
                paires.append({
                    "skills": sorted((a.name, b.name)),
                    "chemins": sorted((a.path, b.path)),
                    "proximite": round(jaccard, 3),
                    "jetons_communs": commun,
                    # Le point qui rend la ligne actionnable.
                    "interdiction": sorted(
                        nom for nom, s in ((a.name, a), (b.name, b)) if s.negatif
                    ),
                })
        paires.sort(key=lambda p: -p["proximite"])
        return paires

    # -- le corpus du banc ---------------------------------------------------

    def information(self, demande: str) -> float:
        """La somme des IDF d'une demande : ce qu'elle porte d'exploitable.

        Relevé du 2026-10-22 : des phrases comme « let me try this and check »
        ou « etape 1 validee, go » figurent dans les declencheurs. Elles ne
        contiennent aucun mot de domaine : ce sont des declencheurs d'ETAT DE
        CONVERSATION, pas de sujet. Aucune recherche par les mots ne peut les
        servir, et les compter comme des echecs de classement designe le mauvais
        remede.
        """
        return sum(self.idf(j) for j in jetons(demande))

    def corpus(self) -> list[tuple[str, str, str]]:
        """Les lignes citees, comme corpus etiquete : `(demande, skill, ligne)`.

        Le bon skill est connu **par construction** : c'est celui qui porte la
        citation. L'etape 8 attendait ce corpus des corrections de la phase 1,
        qui sont a zero ; il existait deja dans la bibliotheque.

        Reserve a dire : une citation curatee est plus propre qu'une demande
        reelle, donc le chiffre sera optimiste par rapport a la production.
        """
        cas: list[tuple[str, str, str]] = []
        for skill in self.skills:
            for ligne in skill.exemples_brut:
                for phrase in _phrases_citees(ligne):
                    if len(jetons(phrase)) >= 2:
                        cas.append((phrase, skill.name, ligne))
        return cas


_CITATION = re.compile(r"[«“\"]\s*([^»”\"]{3,200}?)\s*[»”\"]")


def _phrases_citees(ligne: str) -> list[str]:
    """Les contenus entre guillemets d'une ligne. Une ligne peut en porter
    plusieurs : « point dettes », « point compta »."""
    return [m.group(1).strip() for m in _CITATION.finditer(ligne or "")]
