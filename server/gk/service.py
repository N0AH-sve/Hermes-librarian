"""Le service : la logique des outils, sans une ligne de MCP.

L'etape 6 demande de garder la couche MCP mince et remplacable. Elle l'est donc
ici : ce module ne connait ni transport ni bibliotheque, et c'est lui que les
tests exercent. `server.py` n'est qu'un adaptateur par-dessus.

Un seul outil est expose en phase 1 : `observe`. La chaine de mesure se deploie
avant le brief, pour deux raisons tenues de l'etape 1 et de l'etape 8 — il faut
un point de comparaison, et le corpus du banc d'essai se constitue en marchant.
Effet de bord voulu : toute la plomberie se valide sur une charge qui ne peut
rien casser, puisque rien n'est injecte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .catalog import Catalog
from .corrections import PENDING_SECONDS, Recorder, reconcile_on_start
from .patterns import Detector
from .store import Store, now

logger = logging.getLogger("gk.service")

# Le filtre des messages triviaux appartient a `prepare`, donc a la phase 3. Il
# n'est pas ebauche ici : une constante sans appelant se prend pour une
# fonctionnalite six semaines plus tard.


@dataclass
class Service:
    store: Store
    detector: Detector
    recorder: Recorder
    catalog_: Catalog
    require_token: bool = False

    @classmethod
    def open(cls, database, patterns_path=None, require_token: bool = False) -> "Service":
        store = Store(database)
        reconcile_on_start(store)
        return cls(
            store=store,
            detector=Detector.load(patterns_path),
            recorder=Recorder(store),
            catalog_=Catalog(store),
            require_token=require_token,
        )

    def reload_patterns(self, patterns_path) -> str:
        """Recharge `motifs.yaml` sans redemarrer. L'empreinte change si le jeu
        change, ce qui est precisement ce que `gk stats` surveille."""
        self.detector = Detector.load(patterns_path)
        return self.detector.digest

    # -- outils -------------------------------------------------------------

    def observe(
        self, session_id: str, reply: str, brief_id: int | None = None, turn_id: str = ""
    ) -> dict:
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
            session_id or "", detection, self.detector.digest, turn_id=turn_id or ""
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
        turn_id: str = "",
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
            skill_name, action=action, session_id=session_id, task_id=task_id,
            use_count=use_count, turn_id=turn_id,
        )
        # Si une correction du meme tour attend son attribution, ce skill est
        # celui qui a reellement servi. `turn_id` rend la correlation exacte ;
        # sans lui on retombe sur la plus recente de la session, qui peut viser
        # le mauvais tour quand deux se suivent de pres.
        if turn_id:
            pending = self.store.one(
                "SELECT id FROM corrections WHERE turn_id = ? AND classification = 'pending' "
                "ORDER BY ts DESC LIMIT 1",
                (turn_id,),
            )
        else:
            pending = self.store.one(
                "SELECT id FROM corrections WHERE session_id = ? AND classification = 'pending' "
                "ORDER BY ts DESC LIMIT 1",
                (session_id,),
            )
        attributed = None
        if pending is not None:
            attributed = self.recorder.attribute(int(pending["id"]), skill_name)
        return {"v": 1, "ok": True, "attribution": attributed, "besoins": self._besoins()}

    # -- catalogue ----------------------------------------------------------

    def catalog(self, skills: list[dict], complete: bool = False) -> dict:
        """Recoit le manifeste et rend la liste des tranches voulues.

        Le manifeste est complet par principe, jamais des deltas : GK compare
        les empreintes et ne reclame que ce qui a bouge. Une execution manquee
        retarde donc, elle ne desynchronise pas.

        `complete=False` est le cas d'un manifeste partiel : il n'a pas le droit
        de faire disparaitre un skill, sous peine qu'une page manquante vide le
        catalogue.
        """
        if not isinstance(skills, list):
            return {"v": 1, "ok": False, "erreur": "skills doit etre une liste"}
        resultat = self.catalog_.ingest(skills, complete=bool(complete))
        logger.info(
            "manifeste: %d recus, %d nouveaux, %d modifies, %d disparus, %d a reclamer",
            resultat["recus"], resultat["nouveaux"], resultat["modifies"],
            resultat["disparus"], len(resultat["wanted"]),
        )
        return {"v": 1, "ok": True, **resultat, "besoins": self._besoins()}

    def sections(self, items: list[dict]) -> dict:
        """Recoit les tranches. Elles sont gardees brutes : le decoupage en
        positif et negatif se fait a l'indexation, donc le changer plus tard ne
        demandera pas un nouveau tour de manifeste."""
        if not isinstance(items, list):
            return {"v": 1, "ok": False, "erreur": "items doit etre une liste"}
        resultat = self.catalog_.store_sections(items)
        return {"v": 1, "ok": True, **resultat, "besoins": self._besoins()}

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
        try:
            detail["catalogue"] = self.catalog_.stats()
        except Exception as exc:  # pragma: no cover - chemin de panne
            detail["catalogue"] = {"erreur": repr(exc)}
        # Dire la verite sur l'authentification plutot que de la laisser deviner.
        detail["jeton_exige"] = bool(self.require_token)
        return {"v": 1, "ok": ok, **detail}

    def _besoins(self) -> list[str]:
        """Canal retour : GK signale ses besoins dans les reponses qu'il envoie
        deja, sans jamais parler le premier.

        Regle qui le rend durable : un besoin que le plugin ne connait pas est
        ignore en silence. On peut donc en ajouter autant qu'on veut sans
        toucher au plugin, et c'est ce qui rend celui-ci stable.

        Ne leve jamais : un besoin est un service rendu, pas une raison de faire
        echouer l'appel qui le transporte.
        """
        try:
            besoins: list[str] = []
            if self.catalog_.total() == 0:
                besoins.append("catalogue_vide")
            elif self.catalog_.wanted(limite=1):
                besoins.append("tranches_attendues")
            return besoins
        except Exception as exc:  # pragma: no cover - chemin de panne
            logger.warning("besoins indisponibles: %r", exc)
            return []

    def close(self) -> None:
        self.store.close()
