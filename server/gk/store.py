"""Stockage : une base SQLite, ouverte en WAL, migrée de façon additive.

Trois regles de l'etape 7 sont cablees ici plutot que laissees a la discipline :

* `synchronous=NORMAL` avec WAL, pour sortir les ecritures du chemin critique
  sans sacrifier les faits et les corrections ;
* `integrity_check` au demarrage, puisque SQLite en WAL est robuste mais pas
  invulnerable ;
* migrations **additives uniquement** — on ajoute des colonnes et des tables, on
  n'en supprime ni n'en renomme jamais. C'est ce qui rend le retour a un tag
  d'image precedent possible, faute de pre-production.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2

# Chaque migration est une liste d'instructions, appliquee une fois, dans
# l'ordre. On n'edite JAMAIS une migration deja publiee : on en ajoute une.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        # -- Corrections. Trois niveaux de rétention dans une seule table, pour
        # que `gk forget` puisse effacer le texte sans perdre la mesure.
        """
        CREATE TABLE corrections (
            id                  INTEGER PRIMARY KEY,
            ts                  REAL    NOT NULL,
            -- niveau 1, le compteur : sans limite, aucun texte utilisateur
            session_id          TEXT    NOT NULL DEFAULT '',
            brief_id            INTEGER,
            skill_recommended   TEXT,   -- ce que GK a mis dans le brief
            skill_used          TEXT,   -- ce que l'agent a reellement execute
            skill_expected      TEXT,   -- ce que l'aveu designe : l'etiquette du cas de test
            classification      TEXT    NOT NULL DEFAULT 'pending',
            patterns_digest     TEXT    NOT NULL,
            -- niveau 2, le cas de test : tant que les empreintes des skills tiennent
            request_truncated   TEXT,
            testcase_retired_at REAL,
            -- niveau 3, la preuve : 90 jours
            extract             TEXT,
            extract_expires_at  REAL,
            -- fermeture de l'attribution en attente
            pending_until       REAL
        )
        """,
        "CREATE INDEX idx_corrections_ts ON corrections(ts)",
        "CREATE INDEX idx_corrections_pending ON corrections(pending_until)",
        # -- Journal des briefs : 7 jours, pour attribuer et deboguer.
        """
        CREATE TABLE briefs (
            id          INTEGER PRIMARY KEY,
            ts          REAL NOT NULL,
            session_id  TEXT NOT NULL,
            request_truncated TEXT,
            skills      TEXT,
            arm         TEXT NOT NULL DEFAULT 'none',
            expires_at  REAL NOT NULL
        )
        """,
        "CREATE INDEX idx_briefs_session ON briefs(session_id, ts)",
        "CREATE INDEX idx_briefs_expiry ON briefs(expires_at)",
        # -- Usages de skills, depuis on_skill_lifecycle. Detail 30 jours puis
        # agregat ; le hook porte le changement d'etat, pas le resultat du tour.
        """
        CREATE TABLE skill_uses (
            id          INTEGER PRIMARY KEY,
            ts          REAL NOT NULL,
            session_id  TEXT NOT NULL DEFAULT '',
            task_id     TEXT NOT NULL DEFAULT '',
            skill_name  TEXT NOT NULL,
            action      TEXT NOT NULL DEFAULT '',
            use_count   INTEGER,
            expires_at  REAL NOT NULL
        )
        """,
        "CREATE INDEX idx_uses_skill ON skill_uses(skill_name, ts)",
        """
        CREATE TABLE skill_use_totals (
            skill_name  TEXT PRIMARY KEY,
            uses        INTEGER NOT NULL DEFAULT 0,
            last_ts     REAL
        )
        """,
        # -- Notations et retours d'usage. `source` separe le hook, l'agent et
        # l'humain : les confondre detruirait la confrontation de l'etape 4.
        """
        CREATE TABLE reports (
            id          INTEGER PRIMARY KEY,
            ts          REAL NOT NULL,
            session_id  TEXT NOT NULL DEFAULT '',
            brief_id    INTEGER,
            skill_name  TEXT,
            outcome     TEXT NOT NULL,
            note        TEXT,
            source      TEXT NOT NULL
        )
        """,
        "CREATE INDEX idx_reports_source ON reports(source, ts)",
        # -- Compteurs de service, pour le canari et `gk check`.
        """
        CREATE TABLE counters (
            name    TEXT PRIMARY KEY,
            value   REAL NOT NULL DEFAULT 0
        )
        """,
    ),
    # `turn_id` est fourni par les hooks de Hermes, ce qu'on ignorait en
    # ecrivant la migration 1. Il rend l'attribution d'une correction exacte
    # au lieu de reposer sur la plus recente de la session. Additive, donc une
    # version precedente de GK sait encore lire la base.
    2: (
        "ALTER TABLE corrections ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE skill_uses ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX idx_corrections_turn ON corrections(turn_id)",
    ),
}

VALID_SOURCES = ("hook", "agent", "human")
VALID_CLASSIFICATIONS = ("pending", "bad_recommendation", "brief_ignored", "unattributable")


class IntegrityError(RuntimeError):
    """La base ne passe pas `integrity_check`."""


class Store:
    def __init__(self, path: str | Path, *, check_integrity: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._configure()
        if check_integrity:
            self.check_integrity()
        self.migrate()

    def _configure(self) -> None:
        # WAL : les lectures du chemin de service ne sont pas bloquees par
        # l'ecriture de l'extraction nocturne ou d'un manifeste.
        self.db.execute("PRAGMA journal_mode=WAL")
        # NORMAL : on accepte de perdre les dernieres lignes d'un journal de
        # debogage sur coupure de courant. Les faits et corrections restent
        # ecrits dans des transactions explicites.
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")

    def check_integrity(self) -> None:
        row = self.db.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise IntegrityError(f"integrity_check: {row[0] if row else 'aucun resultat'}")

    # -- migrations ---------------------------------------------------------

    def _current_version(self) -> int:
        self.db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self.db.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"] or 0)

    def migrate(self) -> int:
        """Applique les migrations manquantes. Additives uniquement."""
        version = self._current_version()
        for target in sorted(MIGRATIONS):
            if target <= version:
                continue
            self.db.execute("BEGIN")
            try:
                for statement in MIGRATIONS[target]:
                    self.db.execute(statement)
                self.db.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            version = target
        return version

    # -- compteurs de service ----------------------------------------------

    def bump(self, name: str, *, by: float = 1.0) -> None:
        self.db.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
            (name, by),
        )

    def set_counter(self, name: str, value: float) -> None:
        self.db.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value),
        )

    def counter(self, name: str, default: float = 0.0) -> float:
        row = self.db.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        return float(row["value"]) if row else default

    # -- acces generique ----------------------------------------------------

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.db.execute(sql, tuple(params)).fetchone()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.db.execute(sql, tuple(params))

    def close(self) -> None:
        try:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def now() -> float:
    return time.time()
