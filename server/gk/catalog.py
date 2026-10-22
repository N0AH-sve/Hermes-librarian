"""Le catalogue des skills : reception du manifeste, puis des tranches.

Deux releves ont reduit cette etape a presque rien, et il faut le dire parce que
la conception initiale prevoyait un chantier :

* le prompt systeme de Hermes porte **deja** le catalogue complet
  (`nom : description`, groupe par categories) avec l'ordre de charger au
  moindre doute. GK ne cherche donc pas a informer gb de ce qui existe ;
* l'index ne retient que quatre champs : `name`, `description`, et les deux
  moities des declencheurs. Moins de 300 Ko sur 177 skills, donc pas de FTS5,
  pas de BM25 a regler, pas de corps a transferer.

Le manifeste porte `{path, name, description, category, fingerprint}` pour tous
les skills, jamais des deltas : GK compare et ne reclame les tranches que de
ceux dont l'empreinte a bouge. Le mecanisme est **auto-reparateur** — une
execution manquee retarde, elle ne desynchronise pas — et il gere les
suppressions gratuitement.

L'empreinte vient de `(taille, mtime)` et non d'un hachage du contenu : cote
pont, un manifeste coute 177 `stat` et la lecture du seul frontmatter. Les corps
ne sont lus que pour les skills reclames.

Ce que ce module ne fait pas : decouper les declencheurs en positif et negatif.
Les tranches sont stockees **brutes**, donc changer les regles de decoupage plus
tard ne demandera pas un nouveau tour de manifeste.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import Store, now

# Bornage par appel. Avec ~300 Ko au total ce n'est pas vital, mais cela evite
# qu'un premier manifeste declenche la lecture de 177 fichiers d'un coup dans le
# conteneur de l'agent. Le reste suit au manifeste suivant : la boucle converge.
WANTED_MAX = 40

# Un skill pathologique ne doit pas faire grossir la base sans limite. La
# mediane relevee est de 1,6 Ko par section ; 8000 caracteres laissent large.
MAX_SECTION_CHARS = 8000

# Au-dela, `gk check` doit se plaindre : un catalogue perime se traduit par des
# recommandations sur des skills disparus.
MANIFEST_MAX_AGE_SECONDS = 48 * 3600.0

KIND_RAW = "brut"


@dataclass
class Catalog:
    """Ecrit et relit le catalogue. Ne leve jamais vers l'appelant."""

    store: Store

    # -- reception du manifeste --------------------------------------------

    def ingest(self, skills: list[dict], complete: bool = False) -> dict:
        """Applique un manifeste, en une seule transaction, tout ou rien.

        Un plantage a mi-course n'applique rien, et comme le manifeste est
        auto-reparateur, le suivant repart d'un etat coherent.

        `complete=True` signifie que le manifeste porte la biblioteque entiere :
        les skills absents sont alors marques disparus. Un manifeste partiel ne
        fait jamais disparaitre personne.
        """
        ts = now()
        recus = nouveaux = modifies = invalides = 0
        self.store.execute("BEGIN")
        try:
            vus: list[str] = []
            for entree in skills or []:
                path = str((entree or {}).get("path") or "").strip()
                if not path:
                    invalides += 1
                    continue
                recus += 1
                vus.append(path)
                empreinte = str(entree.get("fingerprint") or "")
                ancien = self.store.one(
                    "SELECT fingerprint FROM skills WHERE path = ?", (path,)
                )
                if ancien is None:
                    nouveaux += 1
                elif ancien["fingerprint"] != empreinte:
                    modifies += 1
                self.store.execute(
                    "INSERT INTO skills (path, name, description, category, fingerprint, "
                    "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(path) DO UPDATE SET name = excluded.name, "
                    "description = excluded.description, category = excluded.category, "
                    "fingerprint = excluded.fingerprint, last_seen = excluded.last_seen, "
                    "missing_since = NULL",
                    (
                        path,
                        str(entree.get("name") or ""),
                        str(entree.get("description") or ""),
                        str(entree.get("category") or ""),
                        empreinte,
                        ts,
                        ts,
                    ),
                )
            disparus = 0
            if complete:
                cur = self.store.execute(
                    "UPDATE skills SET missing_since = ? WHERE last_seen < ? "
                    "AND missing_since IS NULL",
                    (ts, ts),
                )
                disparus = cur.rowcount or 0
            self.store.set_counter("last_catalog_ts", ts)
            self.store.bump("catalog_calls")
            if complete:
                self.store.set_counter("last_complete_catalog_ts", ts)
            self.store.execute("COMMIT")
        except Exception:
            self.store.execute("ROLLBACK")
            raise
        return {
            "recus": recus,
            "nouveaux": nouveaux,
            "modifies": modifies,
            "disparus": disparus,
            "invalides": invalides,
            "wanted": self.wanted(),
        }

    def wanted(self, limite: int = WANTED_MAX) -> list[str]:
        """Les chemins dont GK veut les tranches : jamais indexes, ou modifies.

        Borne a dessein. Le manifeste etant auto-reparateur, ce qui ne passe pas
        cette fois passera au tour suivant.
        """
        rows = self.store.query(
            "SELECT path FROM skills WHERE missing_since IS NULL "
            "AND fingerprint <> indexed_fingerprint ORDER BY last_seen LIMIT ?",
            (max(0, int(limite)),),
        )
        return [r["path"] for r in rows]

    # -- reception des tranches -------------------------------------------

    def store_sections(self, items: list[dict]) -> dict:
        """Enregistre les tranches renvoyees par le pont.

        Une entree **doit** figurer meme quand aucun intitule n'a correspondu,
        avec `sections: []` : sans cela GK reclamerait indefiniment les tranches
        d'un skill qui n'en a aucune.

        Si l'empreinte fournie differe de celle du manifeste, le fichier a bouge
        entre les deux appels : les tranches sont tout de meme gardees, mais le
        skill reste a reclamer. C'est la course que la conception par push devait
        supprimer — elle est reduite, pas abolie.
        """
        ts = now()
        traites = tronques = inconnus = obsoletes = sections_ecrites = 0
        self.store.execute("BEGIN")
        try:
            for item in items or []:
                path = str((item or {}).get("path") or "").strip()
                if not path:
                    continue
                row = self.store.one(
                    "SELECT fingerprint FROM skills WHERE path = ?", (path,)
                )
                if row is None:
                    # Une tranche pour un skill absent du catalogue : on ne
                    # l'invente pas, un manifeste le fera connaitre.
                    inconnus += 1
                    continue
                traites += 1
                self.store.execute("DELETE FROM skill_sections WHERE path = ?", (path,))
                for section in item.get("sections") or []:
                    heading = str((section or {}).get("heading") or "").strip()
                    texte = str((section or {}).get("text") or "")
                    if len(texte) > MAX_SECTION_CHARS:
                        texte = texte[:MAX_SECTION_CHARS]
                        tronques += 1
                    self.store.execute(
                        "INSERT INTO skill_sections (path, kind, heading, text, ts) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(path, kind, heading) DO UPDATE "
                        "SET text = excluded.text, ts = excluded.ts",
                        (path, KIND_RAW, heading, texte, ts),
                    )
                    sections_ecrites += 1
                fournie = str(item.get("fingerprint") or "")
                if fournie and fournie != row["fingerprint"]:
                    obsoletes += 1
                else:
                    self.store.execute(
                        "UPDATE skills SET indexed_fingerprint = fingerprint, indexed_at = ? "
                        "WHERE path = ?",
                        (ts, path),
                    )
            self.store.set_counter("last_sections_ts", ts)
            self.store.execute("COMMIT")
        except Exception:
            self.store.execute("ROLLBACK")
            raise
        return {
            "traites": traites,
            "sections_ecrites": sections_ecrites,
            "tronques": tronques,
            "inconnus": inconnus,
            "obsoletes": obsoletes,
            "wanted": self.wanted(),
        }

    # -- etat ---------------------------------------------------------------

    def total(self) -> int:
        row = self.store.one("SELECT COUNT(*) c FROM skills WHERE missing_since IS NULL")
        return int(row["c"]) if row else 0

    def live_names(self) -> set[str]:
        """Les noms des skills encore au catalogue.

        Remplace le balayage du disque : GK n'a plus le volume de l'agent monte,
        et deux sources de verite sur le catalogue seraient precisement le defaut
        qu'il est cense corriger chez gb.
        """
        rows = self.store.query(
            "SELECT name, path FROM skills WHERE missing_since IS NULL"
        )
        noms: set[str] = set()
        for r in rows:
            if r["name"]:
                noms.add(r["name"])
            # Le dossier porte le meme nom que le frontmatter par convention GM,
            # mais la convention n'est pas garantie : on accepte les deux.
            dossier = str(r["path"]).rstrip("/").split("/")[-1]
            if dossier and dossier != "SKILL.md":
                noms.add(dossier)
            elif dossier == "SKILL.md":
                parts = str(r["path"]).rstrip("/").split("/")
                if len(parts) >= 2:
                    noms.add(parts[-2])
        return noms

    def stats(self) -> dict:
        un = self.store.one
        total = int(un("SELECT COUNT(*) c FROM skills")["c"])
        vivants = self.total()
        indexes = int(
            un(
                "SELECT COUNT(*) c FROM skills WHERE missing_since IS NULL "
                "AND indexed_fingerprint <> '' AND fingerprint = indexed_fingerprint"
            )["c"]
        )
        avec_sections = int(
            un("SELECT COUNT(DISTINCT path) c FROM skill_sections")["c"]
        )
        octets = int(un("SELECT COALESCE(SUM(LENGTH(text)), 0) o FROM skill_sections")["o"])
        dernier = self.store.counter("last_catalog_ts")
        return {
            "skills": vivants,
            "disparus": total - vivants,
            "indexes": indexes,
            "a_indexer": max(0, vivants - indexes),
            "skills_avec_sections": avec_sections,
            "sections_octets": octets,
            "dernier_manifeste_il_y_a_s": round(now() - dernier, 1) if dernier else None,
            "manifeste_perime": bool(
                dernier and (now() - dernier) > MANIFEST_MAX_AGE_SECONDS
            ),
        }
