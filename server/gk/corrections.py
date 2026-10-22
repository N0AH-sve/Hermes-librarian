"""Corrections : enregistrement, attribution, retention a trois niveaux.

Une correction detectee est ambigue, et c'est tout l'enjeu. Elle peut vouloir
dire deux choses opposees :

* **mauvaise recommandation** — GK a propose un skill, l'agent l'a suivi, c'etait
  le mauvais. La recherche est en cause ;
* **brief ignore** — GK avait bien recommande, l'agent a fait autrement. Le
  prompt systeme ou la forme du brief sont en cause.

Meme symptome, remedes opposes. Sans la distinction on risque de passer un mois
a ameliorer un moteur de recherche alors que le probleme est que l'agent ne lit
pas. D'ou la coupure en deux des le premier jour.

Mais l'ordre d'arrivee des evenements n'est pas garanti : `transform_llm_output`
apporte la reponse, `on_skill_lifecycle` le skill reellement touche. Une
correction s'ecrit donc **en attribution en attente** et se complete ensuite ;
au bout de PENDING_SECONDS elle se ferme avec ce qui est connu. Et comme un
redemarrage pourrait la laisser en attente indefiniment, la reconciliation au
demarrage ferme les retardataires.
"""

from __future__ import annotations

from dataclasses import dataclass

from .patterns import MAX_EXTRACT_CHARS, Detection
from .store import Store, now

PENDING_SECONDS = 60.0
BRIEF_TTL_SECONDS = 7 * 86400.0
EXTRACT_TTL_SECONDS = 90 * 86400.0
# Les demandes reelles : meme duree que les preuves, meme troncature.
REQUEST_TTL_SECONDS = 90 * 86400.0
USE_DETAIL_TTL_SECONDS = 30 * 86400.0
REQUEST_TRUNCATE_CHARS = 200

BAD_RECOMMENDATION = "bad_recommendation"
BRIEF_IGNORED = "brief_ignored"
UNATTRIBUTABLE = "unattributable"
PENDING = "pending"


def truncate(text: str | None, limit: int = REQUEST_TRUNCATE_CHARS) -> str | None:
    """Tronque. Ce n'est pas un resume : resumer demanderait un LLM, et il n'y
    en a pas sur ce chemin."""
    if not text:
        return None
    text = " ".join(text.split())
    return text[:limit]


@dataclass
class Recorder:
    """Ecrit les observations. Ne leve jamais vers l'appelant : GK se degrade,
    il n'echoue pas."""

    store: Store

    # -- briefs -------------------------------------------------------------

    def record_brief(
        self, session_id: str, request: str | None, skills: list[str], arm: str = "none"
    ) -> int:
        ts = now()
        cur = self.store.execute(
            "INSERT INTO briefs (ts, session_id, request_truncated, skills, arm, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, session_id, truncate(request), ",".join(skills), arm, ts + BRIEF_TTL_SECONDS),
        )
        return int(cur.lastrowid or 0)

    def last_brief(self, session_id: str) -> dict | None:
        row = self.store.one(
            "SELECT * FROM briefs WHERE session_id = ? ORDER BY ts DESC LIMIT 1", (session_id,)
        )
        return dict(row) if row else None

    # -- demandes reelles ---------------------------------------------------

    def record_request(
        self,
        session_id: str,
        text: str | None,
        turn_id: str = "",
        is_first_turn: bool | None = None,
        platform: str = "",
    ) -> int:
        """Enregistre une demande d'utilisateur, tronquee.

        GK n'avait jamais vu de demande reelle avant le 2026-10-22 : tout ce qui
        avait ete mesure venait de textes ecrits par les auteurs des skills. Le
        texte est tronque a 200 caracteres et expire au bout de 90 jours ; le
        compteur, sans texte, reste sans limite.
        """
        ts = now()
        cur = self.store.execute(
            "INSERT INTO requests (ts, session_id, turn_id, text_truncated, "
            "is_first_turn, platform, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ts, session_id or "", turn_id or "", truncate(text),
                None if is_first_turn is None else int(bool(is_first_turn)),
                platform or "", ts + REQUEST_TTL_SECONDS,
            ),
        )
        self.store.bump("requests_total")
        return int(cur.lastrowid or 0)

    # -- corrections --------------------------------------------------------

    def record_correction(
        self,
        session_id: str,
        detection: Detection,
        patterns_digest: str,
        request: str | None = None,
        turn_id: str = "",
    ) -> int:
        """Enregistre une correction en attribution en attente.

        Trois skills, qu'il ne faut surtout pas confondre :

        * `skill_recommended` — ce que GK a mis dans le brief, lu du journal ;
        * `skill_used` — ce que l'agent a reellement execute, qui n'arrivera
          qu'avec `on_skill_lifecycle`, d'ou l'attente ;
        * `skill_expected` — ce que l'aveu designe (« j'aurais du utiliser X »).
          C'est l'etiquette du cas de test, celle que l'etape 8 decrit comme
          fournie par l'agent lui-meme. Elle ne dit rien de ce qui s'est passe,
          donc elle n'entre pas dans la classification.
        """
        ts = now()
        brief = self.last_brief(session_id)
        recommended = None
        if brief and brief.get("skills"):
            recommended = brief["skills"].split(",")[0] or None
        cur = self.store.execute(
            "INSERT INTO corrections (ts, session_id, turn_id, brief_id, skill_recommended, "
            "skill_expected, classification, patterns_digest, request_truncated, extract, "
            "extract_expires_at, pending_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                session_id,
                turn_id,
                brief["id"] if brief else None,
                recommended,
                detection.skill_hint,
                PENDING,
                patterns_digest,
                truncate(request or (brief or {}).get("request_truncated")),
                detection.extract[:MAX_EXTRACT_CHARS],
                ts + EXTRACT_TTL_SECONDS,
                ts + PENDING_SECONDS,
            ),
        )
        self.store.bump("corrections_total")
        return int(cur.lastrowid or 0)

    def attribute(self, correction_id: int, skill_used: str | None) -> str:
        """Complete une correction avec le skill reellement utilise et la classe."""
        row = self.store.one("SELECT * FROM corrections WHERE id = ?", (correction_id,))
        if row is None:
            return UNATTRIBUTABLE
        classification = _classify(row["skill_recommended"], skill_used or row["skill_used"])
        # `skill_expected` n'entre pas dans la classification : c'est l'etiquette
        # du cas de test, pas un signal sur ce qui s'est passe.
        self.store.execute(
            "UPDATE corrections SET skill_used = COALESCE(?, skill_used), classification = ?, "
            "pending_until = NULL WHERE id = ?",
            (skill_used, classification, correction_id),
        )
        return classification

    def close_pending(self, *, before: float | None = None) -> int:
        """Ferme les attributions en attente echues, avec ce qui est connu.

        Appelee par la reconciliation au demarrage et par le fil de fond. Sans
        elle, un redemarrage laisserait des corrections en attente pour toujours,
        et elles sortiraient des statistiques sans que personne le sache.
        """
        cutoff = before if before is not None else now()
        rows = self.store.query(
            "SELECT id, skill_recommended, skill_used FROM corrections "
            "WHERE classification = ? AND pending_until IS NOT NULL AND pending_until <= ?",
            (PENDING, cutoff),
        )
        for row in rows:
            self.store.execute(
                "UPDATE corrections SET classification = ?, pending_until = NULL WHERE id = ?",
                (_classify(row["skill_recommended"], row["skill_used"]), row["id"]),
            )
        return len(rows)

    # -- usages de skills ---------------------------------------------------

    def record_skill_use(
        self,
        skill_name: str,
        *,
        action: str = "",
        session_id: str = "",
        task_id: str = "",
        use_count: int | None = None,
        turn_id: str = "",
    ) -> None:
        ts = now()
        self.store.execute(
            "INSERT INTO skill_uses (ts, session_id, task_id, turn_id, skill_name, action, "
            "use_count, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, session_id, task_id, turn_id, skill_name, action, use_count,
             ts + USE_DETAIL_TTL_SECONDS),
        )
        # L'agregat survit a la purge du detail : le compteur du brief se
        # construit des usages ici et des echecs cote corrections.
        self.store.execute(
            "INSERT INTO skill_use_totals (skill_name, uses, last_ts) VALUES (?, 1, ?) "
            "ON CONFLICT(skill_name) DO UPDATE SET uses = uses + 1, last_ts = excluded.last_ts",
            (skill_name, ts),
        )

    # -- notations et retours ----------------------------------------------

    def record_report(
        self,
        *,
        skill_name: str | None,
        outcome: str,
        source: str,
        session_id: str = "",
        brief_id: int | None = None,
        note: str | None = None,
    ) -> int:
        cur = self.store.execute(
            "INSERT INTO reports (ts, session_id, brief_id, skill_name, outcome, note, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now(), session_id, brief_id, skill_name, outcome, truncate(note, 500), source),
        )
        return int(cur.lastrowid or 0)


def _classify(recommended: str | None, used: str | None) -> str:
    """Mauvaise recommandation, brief ignore, ou inattribuable.

    Une correction inattribuable reste comptee dans le total et sort du detail :
    le total ne mente jamais.
    """
    if not recommended:
        return UNATTRIBUTABLE
    if not used:
        return UNATTRIBUTABLE
    return BAD_RECOMMENDATION if used == recommended else BRIEF_IGNORED


# -- rangement -------------------------------------------------------------


def tidy(store: Store) -> dict[str, int]:
    """Applique les retentions. Chaque niveau a sa duree, et effacer le texte ne
    perd jamais la mesure."""
    ts = now()
    result = {}
    cur = store.execute("DELETE FROM briefs WHERE expires_at <= ?", (ts,))
    result["briefs_purges"] = cur.rowcount or 0
    cur = store.execute("DELETE FROM skill_uses WHERE expires_at <= ?", (ts,))
    result["usages_detail_purges"] = cur.rowcount or 0
    cur = store.execute("DELETE FROM requests WHERE expires_at <= ?", (ts,))
    result["demandes_purgees"] = cur.rowcount or 0
    # Niveau 3 : la preuve expire, le compteur et le cas de test restent.
    cur = store.execute(
        "UPDATE corrections SET extract = NULL, extract_expires_at = NULL "
        "WHERE extract IS NOT NULL AND extract_expires_at <= ?",
        (ts,),
    )
    result["preuves_effacees"] = cur.rowcount or 0
    return result


def retire_testcases(store: Store, live_skills: set[str]) -> int:
    """Retire les cas de test dont le skill nomme n'est plus au catalogue.

    Une correction vaut tant que les skills qu'elle nomme gardent la meme
    empreinte : au-dela, c'est un fossile qui melange deux etats de la
    bibliotheque. Le compteur, lui, reste sans limite.

    **Refuse d'agir sur un ensemble vide.** Depuis que `live_skills` vient du
    catalogue et non d'un balayage du disque, un catalogue pas encore recu
    donnerait un ensemble vide — et tous les cas de test seraient alors mis au
    rebut d'un coup, puisque aucun nom n'appartient a l'ensemble vide. Le corpus
    du banc est irremplacable : il ne se reconstruit pas.
    """
    if not live_skills:
        return 0
    rows = store.query(
        "SELECT id, skill_expected FROM corrections WHERE testcase_retired_at IS NULL"
    )
    retired = 0
    for row in rows:
        # Seul `skill_expected` compte : c'est l'etiquette. Qu'une ancienne
        # recommandation de GK ait disparu du catalogue n'invalide pas la lecon
        # « pour cette demande, le bon skill etait X ».
        names = {row["skill_expected"]} - {None, ""}
        if names and not names.issubset(live_skills):
            store.execute(
                "UPDATE corrections SET testcase_retired_at = ?, request_truncated = NULL "
                "WHERE id = ?",
                (now(), row["id"]),
            )
            retired += 1
    return retired


def reconcile_on_start(store: Store) -> dict[str, int]:
    """Reconciliation au demarrage, exigee par l'etape 7."""
    recorder = Recorder(store)
    closed = recorder.close_pending()
    tidied = tidy(store)
    store.set_counter("last_reconcile_ts", now())
    return {"attributions_fermees": closed, **tidied}
