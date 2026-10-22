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

# Les intitules que GK demande au pont de decouper. Ils partent dans la reponse
# au manifeste : le pont ne sait pas ce qu'est un declencheur, il tranche sur ces
# chaines. Ajouter une rubrique est donc une ligne de configuration cote GK, et
# plus jamais un redemarrage de l'agent.
#
# Francais et anglais melanges parce que la bibliotheque l'est : le releve du
# 2026-10-22 donnait `when to use` (53) devant `declencheurs` (16).
DEFAULT_HEADINGS: tuple[str, ...] = (
    # Singulier ET pluriel : GM ecrit `## Declencheur: "phrase"`, et le
    # rapprochement etait un test de prefixe, donc `declencheur:` ne commencait
    # pas par `declencheurs` et la section etait perdue en silence. Le pont
    # rapproche desormais les deux sens, mais les deux formes restent listees
    # pour que l'intention se lise.
    "Declencheur",
    "Déclencheur",
    "Declencheurs",
    "Déclencheurs",
    "Quand charger",
    "Quand utiliser",
    "When to use",
    "When to load",
    "Use when",
    "Trigger",
    "Triggers",
    # La moitie negative, quand elle a son propre titre. Dans les 177 skills
    # actuels elle est noyee dans la prose des declencheurs ; le gabarit
    # `gm-skill-template` doit la sortir, et alors le decoupage devient
    # structurel au lieu d'etre un reniflage de negations.
    "Ne pas charger si",
    "Ne pas charger",
    "When not to use",
    "Do not load",
)


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
            # La demande est servie : le manifeste est arrive.
            self._oublier_demande()
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
        titres_repetes = 0
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
                # Deux sections d'un meme skill peuvent porter le MEME titre --
                # GM ecrit 82 sections intitulees `## Declencheur :`. La clef
                # etant (path, kind, heading), la seconde ecrasait la premiere
                # en silence. `kind` porte donc un rang pour les repetitions :
                # rien n'est perdu, le titre reste fidele, et aucune migration
                # n'est necessaire puisque `kind` est deja dans la clef.
                vus: dict[str, int] = {}
                for section in item.get("sections") or []:
                    heading = str((section or {}).get("heading") or "").strip()
                    texte = str((section or {}).get("text") or "")
                    if len(texte) > MAX_SECTION_CHARS:
                        texte = texte[:MAX_SECTION_CHARS]
                        tronques += 1
                    rang = vus.get(heading, 0)
                    vus[heading] = rang + 1
                    kind = KIND_RAW if rang == 0 else f"{KIND_RAW}#{rang + 1}"
                    if rang:
                        titres_repetes += 1
                    self.store.execute(
                        "INSERT INTO skill_sections (path, kind, heading, text, ts) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(path, kind, heading) DO UPDATE "
                        "SET text = excluded.text, ts = excluded.ts",
                        (path, kind, heading, texte, ts),
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
            # Combien de sections partageaient le titre d'une autre dans le meme
            # skill. Ce chiffre aurait du exister des le debut : sans lui, une
            # perte par collision de clef ne laisse aucune trace.
            "titres_repetes": titres_repetes,
            "tronques": tronques,
            "inconnus": inconnus,
            "obsoletes": obsoletes,
            "wanted": self.wanted(),
        }

    # -- etat ---------------------------------------------------------------

    # -- rafraichissement a la demande ------------------------------------

    # GK ne parle jamais le premier : il ne peut pas reclamer un manifeste, il
    # peut seulement le signaler dans une reponse qu'il rend deja. Ce drapeau
    # fait dire `tranches_attendues` au prochain `observe`, et le pont honore ce
    # besoin avec un plancher d'une minute -- sans aucune modification du pont.
    #
    # C'est ce qui permet a une edition de SKILL.md d'etre prise en compte en
    # une minute au lieu d'attendre la ronde horaire.
    DEMANDE = "manifeste_demande"

    def demander_manifeste(self) -> None:
        self.store.set_counter(self.DEMANDE, now())

    def manifeste_demande(self) -> bool:
        return self.store.counter(self.DEMANDE) > 0

    def _oublier_demande(self) -> None:
        self.store.set_counter(self.DEMANDE, 0.0)

    def reindexer(self) -> int:
        """Oublie ce qui a ete indexe, pour que tout soit relu.

        Necessaire chaque fois qu'une regle de LECTURE change -- un intitule
        rapproche autrement, une collision de titres corrigee -- parce que les
        empreintes des fichiers n'ont pas bouge : GK ne redemanderait rien, et
        le correctif ne s'appliquerait qu'aux skills edites ensuite.

        Ne touche ni les corrections ni les compteurs : seul le catalogue se
        reconstruit, et il se reconstruit depuis le pont.
        """
        cur = self.store.execute(
            "UPDATE skills SET indexed_fingerprint = '', indexed_at = NULL "
            "WHERE missing_since IS NULL"
        )
        self.demander_manifeste()
        return cur.rowcount or 0

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
        repetes = int(
            un("SELECT COUNT(*) c FROM skill_sections WHERE kind <> ?", (KIND_RAW,))["c"]
        )
        octets = int(un("SELECT COALESCE(SUM(LENGTH(text)), 0) o FROM skill_sections")["o"])
        dernier = self.store.counter("last_catalog_ts")
        return {
            "skills": vivants,
            "disparus": total - vivants,
            "indexes": indexes,
            "a_indexer": max(0, vivants - indexes),
            "skills_avec_sections": avec_sections,
            "sections_a_titre_repete": repetes,
            "sections_octets": octets,
            "dernier_manifeste_il_y_a_s": round(now() - dernier, 1) if dernier else None,
            "manifeste_perime": bool(
                dernier and (now() - dernier) > MANIFEST_MAX_AGE_SECONDS
            ),
        }
