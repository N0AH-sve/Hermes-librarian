"""Le service : la logique des outils, sans une ligne de MCP.

L'etape 6 demande de garder la couche MCP mince et remplacable. Elle l'est donc
ici : ce module ne connait ni transport ni bibliotheque, et c'est lui que les
tests exercent. `server.py` n'est qu'un adaptateur par-dessus.

Deux outils sont exposes : `observe`, la chaine de mesure, et `optimize`,
l'optimiseur de prompt. L'ordre de deploiement vient de l'etape 1 et de
l'etape 8 — la mesure avant l'aide, pour avoir un point de comparaison et pour
valider la plomberie sur une charge qui ne peut rien casser.

`optimize` est le premier outil a rendre quelque chose que l'agent lira, donc
le premier a pouvoir nuire. Trois garde-fous vivent ici et non dans le plugin,
qui ne comprend rien a ce qu'il transporte :

* **la part injectee** (`optimizer_share`), a zero par defaut. Installer
  l'optimiseur ne change donc rien jusqu'a une decision explicite, et tout est
  deja mesure pendant ce temps-la ;
* **le dedoublonnage** : la meme question n'est pas posee deux fois de suite
  dans une conversation. Sans lui, une demande contradictoire reformulee
  arreterait l'agent a chaque tour ;
* **le plafond** : au plus MAX_ARRETS_PAR_HEURE arrets par conversation et par
  heure, pour qu'une regle trop large coute du bruit et non une conversation.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from .coherence import Coherence, Consigne, charger_coherence
from .corrections import PENDING_SECONDS, Recorder, reconcile_on_start
from .optimizer import SECTION_PROMPT, Lexique, Optimiseur, rendre
from .patterns import Detector
from .regles import charger
from .store import Store, now

logger = logging.getLogger("gk.service")

# Duree pendant laquelle la meme question n'est pas reposee dans une meme
# conversation. Dix minutes : assez pour couvrir la reformulation d'une demande,
# trop court pour masquer une contradiction qui revient une heure plus tard.
COOLDOWN_ARRET = 600.0
MAX_ARRETS_PAR_HEURE = 3
# Les consignes durables ne valent que pour la conversation en cours, et pas
# au-dela de la duree de vie d'un brief : une consigne de la semaine derniere
# n'est plus un contexte, c'est un souvenir, et les souvenirs passent par la
# file de confirmation des faits (phase 4), pas par ici.
CONSIGNE_TTL = 7 * 86400.0
ARRET_TTL = 90 * 86400.0


@dataclass
class Service:
    store: Store
    detector: Detector
    recorder: Recorder
    require_token: bool = False
    optimiseur: Optimiseur | None = None
    coherence: Coherence | None = None
    share: int = 0

    @classmethod
    def open(
        cls,
        database,
        patterns_path=None,
        require_token: bool = False,
        rules_path=None,
        lexicon_path=None,
        share: int = 0,
    ) -> "Service":
        store = Store(database)
        reconcile_on_start(store)
        sections, _ = charger(rules_path)
        return cls(
            store=store,
            detector=Detector.load(patterns_path),
            recorder=Recorder(store),
            require_token=require_token,
            optimiseur=Optimiseur(Lexique.load(lexicon_path), _triviaux(sections)),
            coherence=charger_coherence(rules_path),
            share=_part(share),
        )

    def reload_patterns(self, patterns_path) -> str:
        """Recharge `motifs.yaml` sans redemarrer. L'empreinte change si le jeu
        change, ce qui est precisement ce que `gk stats` surveille."""
        self.detector = Detector.load(patterns_path)
        return self.detector.digest

    def reload_optimizer(self, rules_path, lexicon_path) -> dict[str, str]:
        """Recharge les regles de l'optimiseur et le lexique sans redemarrer.

        Meme discipline que pour les motifs : les deux empreintes changent si
        les fichiers changent, et `gk stats` refuse alors de comparer les
        periodes entre elles.
        """
        sections, _ = charger(rules_path)
        self.coherence = charger_coherence(rules_path)
        self.optimiseur = Optimiseur(Lexique.load(lexicon_path), _triviaux(sections))
        return {"regles": self.coherence.digest, "lexique": self.optimiseur.lexique.digest}

    # -- outils -------------------------------------------------------------

    def observe(self, session_id: str, reply: str, brief_id: int | None = None) -> dict:
        """Recoit la reponse de l'agent et y cherche un aveu.

        Branche sur `transform_llm_output` cote plugin, pas sur `post_llm_call`,
        dont la charge utile ne porte pas le texte de la reponse. Le plugin y
        renvoie toujours None, donc l'observation est sans effet sur l'agent.

        Le texte complet traverse le fil ; seul un extrait borne est conserve.
        """
        self.store.bump("observe_calls")
        self.store.set_counter("last_observe_ts", now())
        detection = self.detector.detect(reply or "")
        if detection is None:
            return {"v": 1, "correction": False, "besoins": self._besoins()}
        correction_id = self.recorder.record_correction(
            session_id or "", detection, self.detector.digest
        )
        logger.info(
            "correction %s detectee (motif=%r, attendu=%r)",
            correction_id, detection.pattern, detection.skill_hint,
        )
        return {
            "v": 1,
            "correction": True,
            "correction_id": correction_id,
            "skill_expected": detection.skill_hint,
            "pending_seconds": PENDING_SECONDS,
            "besoins": self._besoins(),
        }

    def optimize(self, session_id: str, text: str) -> dict:
        """Lit une demande, et rend le bloc a accoler au message.

        Le plugin recoit `block` et ne fait qu'une chose avec : l'accoler. Il ne
        sait pas s'il transporte un arret, une lecture corrigee ou les deux —
        c'est la regle de l'etape 5, et c'est elle qui rend tout changement de
        format gratuit, sans redeploiement chez l'agent.

        Le bloc est **ajoute**, jamais substitue : le message de l'utilisateur
        n'est pas reecrit. Une faute de frappe peut etre un nom propre.
        """
        self.store.bump("optimize_calls")
        self.store.set_counter("last_optimize_ts", now())
        if self.optimiseur is None or self.coherence is None:  # pragma: no cover
            return {"v": 1, "block": "", "besoins": self._besoins()}

        lecture = self.optimiseur.lire(text or "")
        if lecture.trivial:
            # « ok », « merci », « vas-y » : ni correction ni contradiction a
            # chercher, et surtout pas de consigne a retenir d'un acquiescement.
            self.store.bump("optimize_triviaux")
            return {"v": 1, "block": "", "besoins": self._besoins()}

        session_id = str(session_id or "")
        consignes = self._consignes(session_id)
        verdict = self.coherence.examiner(lecture.texte, consignes)

        if verdict.arret:
            etouffe = self._arret_etouffe(session_id, verdict.signature)
            if etouffe:
                # L'arret est enregistre mais pas emis : la mesure doit voir la
                # contradiction meme quand on choisit de ne pas la reposer.
                self._noter_arret(session_id, verdict, emis=False)
                self.store.bump("optimize_arrets_etouffes")
                # La raison est comptee a part, et ce n'est pas du detail : un
                # plafond souvent atteint dit qu'une regle est trop large, un
                # dedoublonnage souvent actif dit que l'agent n'a pas pose la
                # question. Les deux se corrigent a des endroits differents.
                self.store.bump(f"optimize_etouffes_{etouffe}")
                logger.info(
                    "arret etouffe (%s, session=%s, signature=%s)",
                    etouffe, session_id or "?", verdict.signature,
                )
                verdict = _sans_arret(verdict)

        injecte = self._dans_le_bras(session_id)
        bloc = rendre(lecture, verdict) if injecte else ""

        if verdict.arret and injecte:
            self._noter_arret(session_id, verdict, emis=True)
            self.store.bump("optimize_arrets")
            logger.info(
                "arret pose (session=%s, signaux=%s)",
                session_id or "?", [s.regle for s in verdict.signaux],
            )
        elif verdict.question and injecte:
            self.store.bump("optimize_ambiguites")
        if lecture.corrections:
            self.store.bump("optimize_lectures_corrigees")

        # Les consignes se retiennent meme hors du bras : un jour le bras
        # change, et le contexte doit deja etre la.
        self._retenir_consignes(session_id, lecture.texte)

        return {
            "v": 1,
            "block": bloc,
            "prompt_section": SECTION_PROMPT,
            "besoins": self._besoins(),
        }

    # -- les trois garde-fous de `optimize` ---------------------------------

    def _dans_le_bras(self, session_id: str) -> bool:
        """La part injectee, tiree par conversation et non par tour.

        L'etape 8 alterne le brief un tour sur deux. Ici ce serait une faute :
        une conversation ou l'on arrete la demande contradictoire un tour sur
        deux n'est pas un bras de mesure, c'est une incoherence. La decision
        est donc stable pour une conversation entiere, et tiree de son
        identifiant pour rester stable sans rien stocker.
        """
        if self.share >= 100:
            return True
        if self.share <= 0:
            return False
        graine = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]
        return int(graine, 16) % 100 < self.share

    def _arret_etouffe(self, session_id: str, signature: str) -> str:
        ts = now()
        recent = self.store.one(
            "SELECT ts FROM optimizer_stops WHERE session_id = ? AND signature = ? "
            "AND ts > ? ORDER BY ts DESC LIMIT 1",
            (session_id, signature, ts - COOLDOWN_ARRET),
        )
        if recent is not None:
            return "deja_pose"
        combien = self.store.one(
            "SELECT COUNT(*) c FROM optimizer_stops WHERE session_id = ? AND emitted = 1 "
            "AND ts > ?",
            (session_id, ts - 3600.0),
        )
        if combien is not None and int(combien["c"]) >= MAX_ARRETS_PAR_HEURE:
            return "plafond"
        return ""

    def _noter_arret(self, session_id: str, verdict, emis: bool) -> None:
        premier = verdict.signaux[0] if verdict.signaux else None
        ts = now()
        self.store.execute(
            "INSERT INTO optimizer_stops (ts, session_id, kind, rule, signature, question, "
            "extract, emitted, rules_digest, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                session_id,
                premier.genre if premier else "",
                premier.regle if premier else "",
                verdict.signature,
                verdict.question,
                premier.extrait if premier else None,
                1 if emis else 0,
                self.coherence.digest if self.coherence else "",
                ts + ARRET_TTL,
            ),
        )

    # -- les consignes durables ---------------------------------------------

    def _consignes(self, session_id: str) -> list[Consigne]:
        if not session_id:
            return []
        lignes = self.store.query(
            "SELECT rule, side, extract FROM optimizer_constraints WHERE session_id = ? "
            "AND expires_at > ? ORDER BY ts",
            (session_id, now()),
        )
        return [
            Consigne(regle=ligne["rule"], cote=ligne["side"], extrait=ligne["extract"] or "")
            for ligne in lignes
        ]

    def _retenir_consignes(self, session_id: str, texte: str) -> None:
        if not session_id or self.coherence is None:
            return
        for consigne in self.coherence.consignes(texte):
            # Une consigne reaffirmee ne cree pas de doublon : la paire
            # (regle, cote) est ce qui compte, et la plus recente fait foi.
            self.store.execute(
                "DELETE FROM optimizer_constraints WHERE session_id = ? AND rule = ?",
                (session_id, consigne.regle),
            )
            ts = now()
            self.store.execute(
                "INSERT INTO optimizer_constraints (ts, session_id, rule, side, extract, "
                "rules_digest, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    session_id,
                    consigne.regle,
                    consigne.cote,
                    consigne.extrait,
                    self.coherence.digest,
                    ts + CONSIGNE_TTL,
                ),
            )

    def report(
        self,
        skill: str | None,
        outcome: str,
        source: str,
        session_id: str = "",
        note: str | None = None,
    ) -> dict:
        """Enregistre un retour d'usage ou une notation humaine.

        `source` vaut `hook`, `agent` ou `human`, et ne se melange jamais :
        l'etape 4 fait des compteurs automatiques la mesure et des notations
        humaines la calibration. Les confondre detruirait la confrontation qui
        rend le chiffre verifiable.
        """
        if source not in ("hook", "agent", "human"):
            return {"v": 1, "ok": False, "erreur": f"source inconnue: {source!r}"}
        report_id = self.recorder.record_report(
            skill_name=skill, outcome=outcome, source=source, session_id=session_id, note=note
        )
        return {"v": 1, "ok": True, "report_id": report_id, "besoins": self._besoins()}

    def skill_lifecycle(
        self,
        skill_name: str,
        action: str = "",
        session_id: str = "",
        task_id: str = "",
        use_count: int | None = None,
    ) -> dict:
        """Enregistre un evenement de cycle de vie de skill.

        Le hook de Hermes signale un changement d'etat de l'enregistrement, avec
        son `use_count` : c'est la telemetrie du Curator, pas le resultat du
        tour. Le compteur du brief se construit donc d'ici pour les usages, et
        des corrections pour les echecs.
        """
        if not skill_name:
            return {"v": 1, "ok": False}
        self.recorder.record_skill_use(
            skill_name, action=action, session_id=session_id, task_id=task_id, use_count=use_count
        )
        # Si une correction du meme tour attend son attribution, ce skill est
        # celui qui a reellement servi.
        pending = self.store.one(
            "SELECT id FROM corrections WHERE session_id = ? AND classification = 'pending' "
            "ORDER BY ts DESC LIMIT 1",
            (session_id,),
        )
        attributed = None
        if pending is not None:
            attributed = self.recorder.attribute(int(pending["id"]), skill_name)
        return {"v": 1, "ok": True, "attribution": attributed, "besoins": self._besoins()}

    # -- etat ---------------------------------------------------------------

    def health(self) -> dict:
        """Teste vraiment quelque chose, plutot que de repondre 200 a vide.

        L'etape 7 l'exige : un healthcheck qui ne verifie rien laisse passer un
        GK vivant mais casse.
        """
        ok = True
        detail: dict[str, object] = {}
        try:
            detail["schema"] = self.store.migrate()
            detail["corrections"] = int(
                self.store.one("SELECT COUNT(*) c FROM corrections")["c"]
            )
            detail["patterns"] = len(self.detector.raw_patterns)
            detail["patterns_digest"] = self.detector.digest
        except Exception as exc:  # pragma: no cover - chemin de panne
            ok = False
            detail["erreur"] = repr(exc)
        last = self.store.counter("last_observe_ts")
        detail["derniere_observation_il_y_a_s"] = round(now() - last, 1) if last else None
        # L'optimiseur dit son etat, y compris quand il est inactif. Un reglage
        # a zero qui ne se voit pas se prend pour une panne un mois plus tard.
        if self.coherence is not None and self.optimiseur is not None:
            detail["optimiseur"] = {
                "part_injectee": self.share,
                "actif": self.share > 0,
                "regles": self.coherence.source,
                "regles_empreinte": self.coherence.digest,
                "contradictions": len(self.coherence._paires),
                "lexique": self.optimiseur.lexique.source,
                "lexique_empreinte": self.optimiseur.lexique.digest,
                "distance_1_active": self.optimiseur.lexique.distance_1_active,
            }
            dernier = self.store.counter("last_optimize_ts")
            detail["optimiseur"]["derniere_lecture_il_y_a_s"] = (
                round(now() - dernier, 1) if dernier else None
            )
        # Dire la verite sur l'authentification plutot que de la laisser deviner.
        detail["jeton_exige"] = bool(self.require_token)
        return {"v": 1, "ok": ok, **detail}

    def _besoins(self) -> list[str]:
        """Canal retour : GK signale ses besoins dans les reponses qu'il envoie
        deja, sans jamais parler le premier.

        En phase 1 il n'y a pas de catalogue, donc rien a reclamer. La liste
        existe pour que le plugin apprenne a l'ignorer silencieusement — un
        besoin inconnu doit etre sans effet, c'est ce qui rendra les suivants
        gratuits.
        """
        return []

    def close(self) -> None:
        self.store.close()


def _sans_arret(verdict):
    """Le meme verdict, arret retire. `Verdict` est frozen a dessein : un
    etouffement doit produire une nouvelle valeur, pas modifier celle qui a
    deja ete enregistree."""
    from .coherence import Verdict

    return Verdict(
        signaux=verdict.signaux,
        arret=False,
        question=None,
        signature=verdict.signature,
    )


def _triviaux(sections: dict) -> list[str]:
    valeur = sections.get("triviaux")
    return [m for m in valeur if isinstance(m, str)] if isinstance(valeur, list) else []


def _part(valeur) -> int:
    """La part injectee, bornee a 0-100. Une valeur illisible vaut zero : si la
    configuration est douteuse, l'optimiseur n'injecte rien."""
    try:
        return max(0, min(100, int(valeur)))
    except (TypeError, ValueError):
        return 0
