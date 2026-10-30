"""La coherence d'une demande : se contredit-elle, ou contredit-elle le contexte ?

C'est la moitie de l'optimiseur qui ne corrige rien. Elle repond a une seule
question — **cette demande est-elle executable telle quelle ?** — et quand la
reponse est non, elle rend la question a poser plutot qu'une interpretation.

Pourquoi par motifs et non par un modele
----------------------------------------

Meme raison que pour la detection des aveux, et meme contrainte de l'etape 6 :
aucun LLM sur le chemin de service. Un appel de modele a l'entree d'un tour
ajouterait sa latence a chaque message, il faudrait l'attendre pour decider,
et une panne du fournisseur bloquerait l'entree de l'agent. Les motifs sont
rapides, hors ligne, lisibles, et surtout **reglables sans redeployer** : c'est
`optimiseur.yaml` qu'on edite, pas ce fichier.

La contrepartie est assumee : on n'attrape que les contradictions explicites.
Un detecteur qui attrape peu mais ne se trompe pas se laisse utiliser ; un
detecteur bavard se fait desactiver la premiere semaine, et c'est ce qui fixe
toutes les regles de prudence ci-dessous.

Les cinq signaux
----------------

1. **contradiction** — deux cotes opposes d'une meme regle dans le message.
2. **exclusif** — deux valeurs d'un meme choix (production *et* staging).
3. **portee** — « sans toucher a X » puis une action sur X. Le seul signal qui
   lit l'objet de la demande, d'ou son implementation en code et non en regle.
4. **contexte** — la demande va contre une consigne durable posee plus tot dans
   la conversation.
5. **ambiguite** — il manque une information. Pose la question, n'arrete pas.

Les quatre premiers arretent. Le cinquieme se contente de demander, parce que
« il manque une information » est le cas courant et qu'arreter dessus rendrait
l'agent inutilisable.

Les trois regles de prudence, qui comptent autant que les signaux
-----------------------------------------------------------------

**Les suppresseurs sont cherches ENTRE les deux occurrences**, pas n'importe ou
dans le message. « supprime tout sauf septembre » n'est pas une contradiction,
et « d'abord supprime, puis garde une copie » non plus : le « sauf » et le
« puis » resolvent l'opposition, et leur position est ce qui le prouve. Chercher
dans tout le message laisserait un « sauf » lointain desarmer une vraie
contradiction.

**Un changement d'avis explicite annule le signal de contexte.** « finalement,
commit » apres « ne commit jamais » n'est pas une contradiction : c'est un
utilisateur qui change d'avis, et le lui faire confirmer a chaque fois serait
le dressage a l'envers.

**Un seul signal par regle et par message.** Un message qui dit trois fois
« supprime » ne produit pas trois signaux.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .patterns import fold
from .regles import compiler, empreinte

MAX_EXTRAIT = 90
# Regles minimales, utilisees seulement si `optimiseur.yaml` est absent ou
# illisible. Le fichier deploye fait foi, comme pour `motifs.yaml`.
CONTRADICTIONS_DEFAUT: tuple[dict[str, str], ...] = (
    {
        "id": "supprimer-vs-garder",
        "a": r"\b(?:supprim|efface|retir|purge)",
        "b": r"\b(?:garde|conserv|preserv|touche pas)",
        "question": "Faut-il supprimer, ou conserver ?",
    },
    {
        "id": "tout-vs-aucun",
        "a": r"\b(?:tout|tous|toutes)\b",
        "b": r"\b(?:aucun|aucune|rien)\b",
        "question": "« tout » ou « aucun » ? Le message porte les deux.",
    },
)
SUPPRESSEURS_DEFAUT: tuple[str, ...] = (r"\bsauf\b", r"\bd'abord\b", r"\bpuis\b", r"\bau lieu de\b")

# « sans toucher a X », et les actions qui trahissent qu'on y touche quand meme.
PORTEE = re.compile(
    r"(?:sans (?:toucher a|modifier|changer|rien changer a)|ne (?:touche|modifie|change)\w*"
    r"(?: pas)?(?: a)?)\s+(?:la |le |les |l'|au |aux )?([a-z][a-z'-]{2,40})"
)
ACTION_SUR = (
    "supprim", "efface", "modifi", "change", "remplace", "reecri", "renomme",
    "deplace", "ajoute", "corrig", "refactor", "migre", "vide", "purge",
)


@dataclass(frozen=True)
class Signal:
    genre: str  # contradiction | exclusif | portee | contexte | ambiguite
    regle: str
    question: str
    libelle: str
    extrait: str = ""

    @property
    def arrete(self) -> bool:
        return self.genre != "ambiguite"


@dataclass(frozen=True)
class Verdict:
    signaux: tuple[Signal, ...] = ()
    arret: bool = False
    question: str | None = None
    signature: str = ""


@dataclass(frozen=True)
class Consigne:
    """Une consigne durable retenue d'un tour anterieur. Ce qui est garde est
    minimal a dessein : la regle, le cote, et un extrait borne."""

    regle: str
    cote: str  # "a" ou "b"
    extrait: str = ""


class Coherence:
    """Charge les regles, et examine un message. Ne touche jamais au stockage :
    le service s'occupe des consignes et des arrets deja poses."""

    def __init__(self, sections: dict | None = None, *, source: str = "defauts"):
        sections = sections or {}
        self.source = source
        self.contradictions = _mappings(sections.get("contradictions")) or list(
            CONTRADICTIONS_DEFAUT
        )
        self.ambiguites = _mappings(sections.get("ambiguites"))
        self.exclusifs = [
            [part.strip() for part in ligne.split("|") if part.strip()]
            for ligne in _chaines(sections.get("exclusifs"))
        ]
        self.suppresseurs = compiler(_chaines(sections.get("suppresseurs")) or list(SUPPRESSEURS_DEFAUT))
        self.changement_d_avis = compiler(_chaines(sections.get("changement_d_avis")))
        self.consignes_durables = compiler(_chaines(sections.get("consignes_durables")))

        self._paires = [
            (regle, _compile_un(regle.get("a")), _compile_un(regle.get("b")))
            for regle in self.contradictions
        ]
        self._paires = [(r, a, b) for r, a, b in self._paires if a and b]
        self._exclusifs = [
            [(valeur, _compile_un(valeur)) for valeur in groupe] for groupe in self.exclusifs
        ]
        self._ambiguites = [
            (regle, _compile_un(regle.get("a")), _compile_un(regle.get("sauf")))
            for regle in self.ambiguites
        ]
        self._ambiguites = [(r, a, s) for r, a, s in self._ambiguites if a]

    @property
    def digest(self) -> str:
        """Empreinte des regles effectivement retenues — pas du fichier : une
        regle invalide est ignoree, et ce qui doit etre enregistre avec un arret
        est ce qui a decide, pas ce qui etait ecrit."""
        return empreinte(
            "\n".join(f"{r.get('id', '')}|{r.get('a', '')}|{r.get('b', '')}" for r, _, _ in self._paires),
            "\n".join("|".join(v for v, _ in groupe) for groupe in self._exclusifs),
            "\n".join(f"{r.get('id', '')}|{r.get('a', '')}" for r, _, _ in self._ambiguites),
            "\n".join(m for m, _ in self.suppresseurs),
            "\n".join(m for m, _ in self.changement_d_avis),
        )

    # -- examen -------------------------------------------------------------

    def examiner(self, texte: str, consignes: list[Consigne] | None = None) -> Verdict:
        if not isinstance(texte, str) or not texte.strip():
            return Verdict()
        replie = fold(texte)
        signaux: list[Signal] = []
        signaux += self._contradictions_internes(replie, texte)
        signaux += self._valeurs_exclusives(replie, texte)
        signaux += self._portee(replie)
        signaux += self._contre_le_contexte(replie, consignes or [])
        if not signaux:
            # Une ambiguite ne se pose que si rien de plus grave n'a ete trouve :
            # une question de precision par-dessus un arret serait du bruit.
            signaux += self._ambigu(replie)

        arret = any(s.arrete for s in signaux)
        question = signaux[0].question if signaux else None
        return Verdict(
            signaux=tuple(signaux),
            arret=arret,
            question=question,
            signature=empreinte(*(f"{s.genre}:{s.regle}" for s in sorted(signaux, key=_cle))),
        )

    def consignes(self, texte: str) -> list[Consigne]:
        """Les consignes durables que ce message pose pour la suite.

        Une phrase n'est retenue que si elle porte a la fois un cote d'une regle
        de contradiction et un marqueur de duree (« toujours », « jamais »,
        « par defaut »). « ne commit pas » est une demande du moment ; « ne
        commit jamais sans me demander » est une consigne.
        """
        if not isinstance(texte, str) or not texte.strip():
            return []
        replie = fold(texte)
        marqueurs = [m for _, motif in self.consignes_durables for m in [motif.search(replie)] if m]
        if not marqueurs:
            return []
        out: list[Consigne] = []
        for regle, a, b in self._paires:
            retenus = []
            for cote, motif in (("a", a), ("b", b)):
                trouve = motif.search(replie)
                if not trouve:
                    continue
                # Le marqueur de duree doit etre dans la meme phrase que le cote
                # trouve, sinon « toujours » en fin de message rendrait durable
                # n'importe quelle demande du debut.
                if not any(_meme_phrase(replie, trouve.start(), m.start()) for m in marqueurs):
                    continue
                if _negation_avant(replie, trouve.start()):
                    # Polarite indecidable, et c'est le cas le plus courant en
                    # francais : « ne commit JAMAIS SANS me demander » porte deux
                    # negations, et le cote « sans me demander » y veut dire
                    # exactement le contraire de ce qu'il dit seul. Une consigne
                    # mal signee produirait un arret a contresens trois tours
                    # plus tard, donc on n'en retient aucune.
                    continue
                retenus.append((cote, trouve))
            if len(retenus) != 1:
                continue
            cote, trouve = retenus[0]
            out.append(
                Consigne(
                    regle=str(regle.get("id") or regle.get("a") or ""),
                    cote=cote,
                    extrait=_extrait(replie, trouve.start()),
                )
            )
        return out

    # -- les cinq signaux ---------------------------------------------------

    def _contradictions_internes(self, replie: str, texte: str) -> list[Signal]:
        out: list[Signal] = []
        for regle, a, b in self._paires:
            ma, mb = a.search(replie), b.search(replie)
            if not ma or not mb:
                continue
            if self._supprime(replie, ma, mb):
                continue
            out.append(
                Signal(
                    genre="contradiction",
                    regle=str(regle.get("id") or ""),
                    question=str(regle.get("question") or _question_generique()),
                    libelle=(
                        f"Contradiction dans la demande : "
                        f"« {_extrait(replie, ma.start(), borne=mb.start())} » et "
                        f"« {_extrait(replie, mb.start(), borne=ma.start())} »."
                    ),
                    extrait=_extrait(replie, min(ma.start(), mb.start()), largeur=MAX_EXTRAIT),
                )
            )
        return out

    def _valeurs_exclusives(self, replie: str, texte: str) -> list[Signal]:
        out: list[Signal] = []
        for groupe in self._exclusifs:
            trouves = [(valeur, m) for valeur, motif in groupe if (m := motif.search(replie))]
            if len(trouves) < 2:
                continue
            (v1, m1), (v2, m2) = trouves[0], trouves[1]
            if self._supprime(replie, m1, m2):
                continue
            out.append(
                Signal(
                    genre="exclusif",
                    regle="|".join(v for v, _ in groupe),
                    question=(
                        f"Deux choix incompatibles apparaissent : « {m1.group(0)} » et "
                        f"« {m2.group(0)} ». Lequel faut-il retenir ?"
                    ),
                    libelle=(
                        f"Choix incompatibles : « {m1.group(0)} » et « {m2.group(0)} »."
                    ),
                )
            )
        return out

    def _portee(self, replie: str) -> list[Signal]:
        out: list[Signal] = []
        for m in PORTEE.finditer(replie):
            objet = m.group(1)
            if len(objet) < 4:
                continue
            for autre in re.finditer(re.escape(objet), replie):
                if autre.start() == m.end(0) - len(objet):
                    continue
                contexte = replie[max(0, autre.start() - 60) : autre.start()]
                if not any(verbe in contexte for verbe in ACTION_SUR):
                    continue
                out.append(
                    Signal(
                        genre="portee",
                        regle=f"portee:{objet}",
                        question=(
                            f"Le message demande de ne pas toucher a « {objet} », et une action "
                            f"porte quand meme dessus. Faut-il y toucher, oui ou non ?"
                        ),
                        libelle=f"« sans toucher a {objet} », mais une action porte sur {objet}.",
                    )
                )
                break
        return out

    def _contre_le_contexte(self, replie: str, consignes: list[Consigne]) -> list[Signal]:
        if not consignes:
            return []
        if any(motif.search(replie) for _, motif in self.changement_d_avis):
            # Changement d'avis explicite : c'est la reponse, pas une
            # contradiction. On ne fait pas confirmer un utilisateur qui vient
            # de dire « finalement ».
            return []
        par_id = {str(r.get("id") or ""): (r, a, b) for r, a, b in self._paires}
        out: list[Signal] = []
        vues: set[str] = set()
        for consigne in consignes:
            trio = par_id.get(consigne.regle)
            if trio is None or consigne.regle in vues:
                continue
            regle, a, b = trio
            oppose = b if consigne.cote == "a" else a
            trouve = oppose.search(replie)
            if not trouve:
                continue
            vues.add(consigne.regle)
            out.append(
                Signal(
                    genre="contexte",
                    regle=consigne.regle,
                    # La question ne repete pas le libelle : le bloc affiche les
                    # deux, et une question qui recite son propre contexte se lit
                    # deux fois pour rien dans celui de l'agent.
                    question=str(regle.get("question") or _question_generique()),
                    libelle=(
                        f"Contredit une consigne posee plus tot dans cette conversation : "
                        f"« {consigne.extrait} »."
                    ),
                    extrait=_extrait(replie, trouve.start()),
                )
            )
        return out

    def _ambigu(self, replie: str) -> list[Signal]:
        for regle, a, sauf in self._ambiguites:
            if not a.search(replie):
                continue
            if sauf is not None and sauf.search(replie):
                continue
            return [
                Signal(
                    genre="ambiguite",
                    regle=str(regle.get("id") or ""),
                    question=str(regle.get("question") or _question_generique()),
                    libelle=str(regle.get("question") or ""),
                )
            ]
        return []

    # -- prudence -----------------------------------------------------------

    def _supprime(self, replie: str, ma: re.Match[str], mb: re.Match[str]) -> bool:
        """Un suppresseur resout l'opposition s'il la gouverne.

        La fenetre va du **debut de la phrase** jusqu'a la **seconde**
        occurrence, et pas au-dela. Les deux bornes portent chacune une
        decision, apprise sur des faux positifs :

        * **a gauche jusqu'au debut de la phrase**, parce qu'un suppresseur
          gouverne ce qui le suit : dans « compare production et staging » et
          dans « au lieu de supprimer, garde les logs », le mot qui resout
          l'opposition precede les deux occurrences. Ne regarder qu'entre elles
          laissait ces deux phrases declencher un arret ;
        * **pas au-dela de la seconde**, parce qu'un « sauf » d'une phrase
          suivante ne dit rien de cette paire-la et ne doit pas pouvoir
          desarmer une vraie contradiction.
        """
        gauche, droite = min(ma.start(), mb.start()), max(ma.start(), mb.start())
        phrase = replie.rfind("\n", 0, gauche)
        for ponctuation in (".", ";", "!", "?"):
            phrase = max(phrase, replie.rfind(ponctuation, 0, gauche))
        fenetre = replie[phrase + 1 : max(droite, min(ma.end(), mb.end())) + 10]
        return any(motif.search(fenetre) for _, motif in self.suppresseurs)


# -- chargement et utilitaires ---------------------------------------------


def charger_coherence(chemin) -> Coherence:
    from .regles import charger

    sections, source = charger(chemin)
    return Coherence(sections, source=source)


def _mappings(valeur) -> list[dict[str, str]]:
    if not isinstance(valeur, list):
        return []
    return [element for element in valeur if isinstance(element, dict)]


def _chaines(valeur) -> list[str]:
    if not isinstance(valeur, list):
        return []
    return [element for element in valeur if isinstance(element, str)]


def _compile_un(motif) -> re.Pattern[str] | None:
    if not isinstance(motif, str) or not motif.strip():
        return None
    try:
        return re.compile(motif)
    except re.error:
        return None


# Une conjonction en fin d'extrait annonce la suite de la phrase, qui n'y est
# pas. La couper se voit tout de suite a la lecture du bloc.
QUEUE = re.compile(r"[\s,;:]*\b(?:et|puis|ensuite|mais|donc|ou|car|pour|avec|sans)\s*$")


def _extrait(replie: str, position: int, largeur: int = 44, borne: int | None = None) -> str:
    """L'extrait part de l'occurrence et va vers la droite.

    Partir de l'occurrence et non l'entourer change beaucoup a la lecture : la
    question affichee montre alors « supprime tous les logs » et « garde ceux
    de septembre », deux morceaux distincts, au lieu de deux fenetres qui se
    chevauchent et disent la meme chose.

    Pris sur le texte replie et non sur l'original : `fold` preserve les
    positions pour le francais, mais pas pour tout l'Unicode, et un extrait
    decale d'un caractere serait pire qu'un extrait sans accents.
    """
    fin = position + largeur
    if borne is not None and position < borne < fin:
        fin = borne
    morceau = replie[position:fin]
    coupe = re.search(r"[.;!?\n]", morceau)
    if coupe:
        morceau = morceau[: coupe.start()]
    elif len(replie) > position + largeur and " " in morceau:
        morceau = morceau[: morceau.rindex(" ")]
    plat = " ".join(morceau.split()).strip(" ,;:-")
    return QUEUE.sub("", plat).strip(" ,;:-")


# Ce qui, place juste avant une occurrence, en inverse le sens.
NEGATEURS = re.compile(r"\b(?:ne|n'|pas|jamais|sans|aucun|aucune|rien|plus)\b")


def _negation_avant(replie: str, position: int, fenetre: int = 22) -> bool:
    return bool(NEGATEURS.search(replie[max(0, position - fenetre) : position]))


def _meme_phrase(replie: str, a: int, b: int) -> bool:
    entre = replie[min(a, b) : max(a, b)]
    return not re.search(r"[.!?\n]", entre)


def _question_generique() -> str:
    return "La demande se contredit. Que faut-il faire exactement ?"


def _cle(signal: Signal) -> tuple[str, str]:
    return signal.genre, signal.regle
