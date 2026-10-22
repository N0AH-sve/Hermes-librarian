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
