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

from .catalog import DEFAULT_HEADINGS, Catalog
from .corrections import (
    PENDING_SECONDS,
    REQUEST_MAX_CHARS,
    Recorder,
    reconcile_on_start,
)
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
    headings: tuple[str, ...] = DEFAULT_HEADINGS
    request_max_chars: int = REQUEST_MAX_CHARS

    @classmethod
    def open(
        cls,
        database,
        patterns_path=None,
        require_token: bool = False,
        headings: tuple[str, ...] | None = None,
        request_max_chars: int = REQUEST_MAX_CHARS,
    ) -> "Service":
        store = Store(database)
        reconcile_on_start(store)
        return cls(
            store=store,
            detector=Detector.load(patterns_path),
            recorder=Recorder(store),
            catalog_=Catalog(store),
            require_token=require_token,
            headings=tuple(headings) if headings else DEFAULT_HEADINGS,
            request_max_chars=max(50, int(request_max_chars or REQUEST_MAX_CHARS)),
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

    def request(
        self,
        session_id: str,
        text: str,
        turn_id: str = "",
        is_first_turn: bool | None = None,
        platform: str = "",
    ) -> dict:
        """Enregistre une demande reelle. **N'injecte rien et ne rend rien** qui
        puisse changer le comportement de l'agent.

        Branche sur `pre_llm_call`, le hook qui porte la demande de
        l'utilisateur. C'est le seul endroit ou elle passe : `transform_llm_output`
        porte la reponse, et `on_skill_lifecycle` le skill charge. GK etait donc
        structurellement aveugle aux demandes, et tout ce qui avait ete mesure
        venait de textes ecrits par les auteurs des skills.

        **Ce hook est sur le chemin critique** : il precede l'appel au modele,
        donc tout ce qui attend ici fait attendre l'utilisateur. Le pont envoie
        sur un fil detache et renvoie None immediatement ; cette methode ne doit
        de son cote rien faire de lent.
        """
        if not isinstance(text, str) or not text.strip():
            return {"v": 1, "ok": False, "besoins": self._besoins()}
        self.store.bump("request_calls")
        self.store.set_counter("last_request_ts", now())
        request_id = self.recorder.record_request(
            session_id or "", text, turn_id=turn_id or "",
            is_first_turn=is_first_turn, platform=platform or "",
            limite=self.request_max_chars,
        )
        # Aucun contenu n'est rendu : la phase d'observation ne doit pas pouvoir
        # devenir une injection par accident.
        return {"v": 1, "ok": True, "request_id": request_id, "besoins": self._besoins()}

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
        # celui qui a reellement servi.
        #
        # RELEVE DU 2026-10-22 : `on_skill_lifecycle` NE PORTE PAS `turn_id`.
        # Ses kwargs reels sont {action, provenance, reuse_after_patch, reused,
        # session_id, skill_name, task_id, telemetry_schema_version, use_count}.
        # `transform_llm_output` en a un, lui. Les deux evenements qu'il faut
        # rapprocher n'ont donc aucune clef commune hors `session_id`, et le
        # chemin par `turn_id` ci-dessous ne se declenche jamais en production.
        # Il est garde parce qu'il ne coute rien et marcherait si Hermes
        # ajoutait la clef.
        #
        # Le repli par session est acceptable tant que les tours d'une meme
        # session ne se chevauchent pas : `on_skill_lifecycle` precede la
        # reponse, donc le dernier skill charge avant une correction est
        # vraisemblablement le bon. Il devient faux si gb traite deux tours en
        # parallele dans une seule session.
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

    def consulter(
        self, demande: str, session_id: str = "", limite: int = 5
    ) -> dict:
        """Une consultation **demandee par un humain**, et sa trace.

        **Pourquoi ceci ne contredit pas l'etape 2 de la conception.** Le refus
        d'origine -- « ne pas donner a l'agent un outil qu'il appellerait » --
        portait sur l'AGENT : une machine qui oublie de verifier oublie aussi de
        demander. Un humain qui tape `/gk` n'oublie rien, il a decide. Le refus
        ne s'applique pas, et je l'avais ecrit pour l'autre cas.

        Trois proprietes qu'aucun seuil n'atteindra :

        * **le droit de se taire est resolu**, parce qu'il est delegue a la
          personne. Elle connait son intention ; aucun signal du score ne la
          connait ;
        * **le risque est nul sur les tours ordinaires** : rien n'est injecte
          nulle part tant que personne ne demande ;
        * **chaque consultation suivie d'un retour est une etiquette humaine**,
          produite au fil du travail. C'est la vérité terrain que le projet
          n'arrive pas a obtenir depuis deux jours, et elle s'accumule sans
          seance d'annotation.

        Une reserve a garder : on tape `/gk` quand on doute, donc ces demandes
        sont **plus dures que la moyenne**. Le rappel mesure dessus sera
        pessimiste, ce qui est le bon sens de l'erreur.
        """
        from .recherche import Index, contexte_de_session

        index = Index.construire(self.store)
        if index.n == 0:
            return {"v": 1, "ok": False, "erreur": "catalogue_vide"}
        contexte = contexte_de_session(self.store, session_id) if session_id else []
        if contexte:
            resultats = index.chercher_avec_contexte(
                demande, contexte=contexte, limite=limite
            )
        else:
            resultats = index.chercher(demande, limite=limite)
        descriptions = {
            r["name"]: (r["description"] or "")
            for r in self.store.query(
                "SELECT name, description FROM skills WHERE missing_since IS NULL"
            )
        }
        propositions = [
            {
                "rang": i,
                "name": r["name"],
                "description": descriptions.get(r["name"], ""),
                "score": r["score"],
            }
            for i, r in enumerate(resultats, start=1)
        ]
        # La consultation est tracee pour qu'un retour puisse s'y rattacher :
        # sans ce lien, « c'etait le bon » ne designerait rien.
        #
        # `skill_name` porte **la liste complete** des propositions, separees
        # par des virgules, et non la seule premiere. C'est elle qui permet de
        # retrouver a quel rang se trouvait le bon skill quand un collegue dit
        # seulement « ca m'a servi » -- il ne connait pas les 186 noms, donc on
        # ne peut pas lui demander de nommer.
        #
        # Pourquoi ici et pas dans `briefs`, qui a une colonne `skills` faite
        # pour ca : les briefs expirent a sept jours, et une etiquette humaine
        # est la donnee la plus couteuse du projet. Elle ne doit pas expirer.
        consultation_id = self.recorder.record_report(
            skill_name=",".join(p["name"] for p in propositions) or None,
            outcome="consultation",
            source="hook",
            session_id=session_id,
            note=demande,
        )
        return {
            "v": 1,
            "ok": True,
            "consultation_id": consultation_id,
            "propositions": propositions,
            "contexte_tours": len(contexte),
        }

    def retour(
        self,
        consultation_id: int | None,
        choix: str,
        session_id: str = "",
        propositions: list[str] | None = None,
    ) -> dict:
        """Le retour humain sur une consultation : une etiquette, au fil du travail.

        `choix` est un rang (« 3 »), un nom de skill, ou « 0 » pour « aucun ne
        convient ». La meme grammaire que l'echantillon annote, pour que les
        deux corpus se lisent ensemble.

        Enregistre avec `source='human'`, qui ne se melange jamais avec les
        compteurs automatiques : l'etape 4 fait des uns la mesure et des autres
        la calibration, et les confondre detruirait la confrontation.
        """
        brut = (choix or "").strip()
        if not brut:
            return {"v": 1, "ok": False, "erreur": "choix_vide"}

        # Deux grammaires, et la premiere est celle des collegues.
        #
        # **Un collegue ne sait pas quel skill a ete charge** : il ne le voit
        # pas, et il y en a 186. Lui demander d'en nommer un n'avait aucun sens,
        # et c'est le defaut que GM a releve en relisant la note de consignes.
        # Il juge donc ce qu'il a sous les yeux -- la reponse de gb -- et le lien
        # avec le skill se fait de notre cote, par la liste des propositions
        # gardee sur la consultation.
        #
        # La seconde grammaire reste : un rang ou un nom, pour qui connait la
        # bibliotheque. Les deux ne mesurent pas la meme chose, et
        # `gk verdicts` les rend separement plutot que de les additionner.
        verdicts = {
            "servi": "servi", "sert": "servi", "oui": "servi", "bon": "servi",
            "a-cote": "a_cote", "a_cote": "a_cote", "acote": "a_cote",
            "non": "a_cote", "mauvais": "a_cote", "rate": "a_cote",
            "inconnu": "inconnu", "?": "inconnu", "sais-pas": "inconnu",
            "rien": "aucun_ne_convient", "aucun": "aucun_ne_convient",
        }
        bas = brut.lower()
        if bas in verdicts:
            verdict = verdicts[bas]
            report_id = self.recorder.record_report(
                skill_name=None,
                outcome=verdict,
                source="human",
                session_id=session_id,
                note=(f"consultation {consultation_id}" if consultation_id else None),
                brief_id=consultation_id,
            )
            return {
                "v": 1, "ok": True, "report_id": report_id,
                "verdict": verdict, "etiquette": None,
                "porte": (
                    "la reponse de gb de bout en bout, pas le rang de GK"
                    if verdict in ("servi", "a_cote") else "rien a mesurer"
                ),
            }

        nom = None
        if brut.isdigit():
            rang = int(brut)
            if rang == 0:
                nom = None
            elif propositions and 1 <= rang <= len(propositions):
                nom = propositions[rang - 1]
            else:
                return {"v": 1, "ok": False, "erreur": "rang_hors_des_propositions"}
        else:
            nom = brut
        report_id = self.recorder.record_report(
            skill_name=nom,
            outcome=("aucun_ne_convient" if nom is None else "bon_skill"),
            source="human",
            session_id=session_id,
            note=(f"consultation {consultation_id}" if consultation_id else None),
            brief_id=consultation_id,
        )
        return {
            "v": 1, "ok": True, "report_id": report_id, "etiquette": nom,
            "verdict": ("aucun_ne_convient" if nom is None else "bon_skill"),
            "porte": "le rang de GK, donc sa justesse",
        }

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
        # Les intitules partent avec la reponse : le pont tranche sur des
        # chaines qu'il ne comprend pas, et en ajouter une plus tard ne
        # demandera ni modification du pont ni redemarrage de l'agent.
        return {
            "v": 1,
            "ok": True,
            **resultat,
            "headings": list(self.headings),
            "besoins": self._besoins(),
        }

    def sections(self, items: list[dict]) -> dict:
        """Recoit les tranches. Elles sont gardees brutes : le decoupage en
        positif et negatif se fait a l'indexation, donc le changer plus tard ne
        demandera pas un nouveau tour de manifeste."""
        if not isinstance(items, list):
            return {"v": 1, "ok": False, "erreur": "items doit etre une liste"}
        resultat = self.catalog_.store_sections(items)
        return {
            "v": 1,
            "ok": True,
            **resultat,
            "headings": list(self.headings),
            "besoins": self._besoins(),
        }

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
            elif self.catalog_.wanted(limite=1) or self.catalog_.manifeste_demande():
                # `manifeste_demande` est pose par `gk rafraichir`. C'est le seul
                # moyen pour GK de hater un manifeste sans parler le premier, et
                # il ne demande aucune modification du pont.
                besoins.append("tranches_attendues")
            return besoins
        except Exception as exc:  # pragma: no cover - chemin de panne
            logger.warning("besoins indisponibles: %r", exc)
            return []

    def close(self) -> None:
        self.store.close()
