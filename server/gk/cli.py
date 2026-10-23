"""La commande `gk`. Elle tourne dans le conteneur, aucun port n'etant publie
au-dela de la boucle locale.

Les commandes :

* `stats`     — l'indicateur, coupe en deux comme l'etape 2 l'exige, et qui
                **refuse de comparer** deux periodes mesurees avec des motifs
                differents ;
* `check`     — l'etat, pour le rituel de mise a jour en trois temps ;
* `catalogue` — l'etat du catalogue recu par manifeste ;
* `tidy`      — appliquer les retentions ;
* `forget`    — effacer le texte sans perdre la mesure ;
* `export`    — les quatre tables irremplacables, et elles seules ;
* `patterns`  — les motifs charges et leur empreinte.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
import re
import sys
from pathlib import Path

from .catalog import Catalog
from .config import Config
from .declencheurs import decouper, lecture, mesurer
from .corrections import (
    BAD_RECOMMENDATION,
    BRIEF_IGNORED,
    UNATTRIBUTABLE,
    retire_testcases,
    tidy,
)
from .patterns import Detector, fold
from .recherche import POIDS, POIDS_CONTEXTE, Index, contexte_de_session
from .service import Service
from .store import Store, now

# Les quatre tables qui ne se reconstruisent pas. Tout le reste derive d'un
# manifeste ou se jette, d'ou une sauvegarde qui tient en quelques dizaines de
# kilo-octets.
IRREMPLACABLE = ("corrections", "reports", "skill_use_totals", "counters")


def cmd_stats(args, cfg: Config) -> int:
    with Store(cfg.database) as store:
        groups = store.query(
            "SELECT patterns_digest, COUNT(*) n, MIN(ts) debut, MAX(ts) fin, "
            "SUM(classification = ?) mauvaise_reco, SUM(classification = ?) brief_ignore, "
            "SUM(classification = ?) inattribuable "
            "FROM corrections GROUP BY patterns_digest ORDER BY debut",
            (BAD_RECOMMENDATION, BRIEF_IGNORED, UNATTRIBUTABLE),
        )
        observes = store.counter("observe_calls")
        out = {
            "messages_observes": int(observes),
            "periodes": [
                {
                    "empreinte_motifs": g["patterns_digest"],
                    "corrections": g["n"],
                    "mauvaise_recommandation": g["mauvaise_reco"],
                    "brief_ignore": g["brief_ignore"],
                    "inattribuable": g["inattribuable"],
                    "debut": _iso(g["debut"]),
                    "fin": _iso(g["fin"]),
                }
                for g in groups
            ],
        }
        if observes:
            out["corrections_pour_100_messages"] = round(
                sum(g["n"] for g in groups) * 100 / observes, 2
            )
        if len(groups) > 1:
            # Le refus est le point de la commande, pas une precaution : un
            # motif ajoute au milieu d'une mesure fait monter le chiffre sans
            # que rien n'ait change chez l'agent.
            out["comparaison"] = (
                f"REFUSEE — {len(groups)} jeux de motifs differents. Les periodes ne sont pas "
                "comparables entre elles ; comparez a empreinte egale."
            )
        elif not groups:
            # Dire qu'une comparaison est possible quand il n'y a rien a
            # comparer, c'est une ligne rassurante qui ne dit rien. Le cas
            # s'est produit au premier releve reel : 3 messages, 0 correction.
            out["comparaison"] = (
                "rien a comparer : aucune correction detectee"
                + (f" sur {observes:g} message(s) observe(s)" if observes else "")
            )
        else:
            out["comparaison"] = "possible, un seul jeu de motifs"
        _emit(out, args)
    return 0


def cmd_check(args, cfg: Config) -> int:
    """Detecte une coupure silencieuse. Un GK vivant mais deconnecte repond
    normalement : seule l'absence d'appels le trahit."""
    problemes: list[str] = []
    detail: dict[str, object] = {}
    try:
        svc = Service.open(
            cfg.database, cfg.patterns, require_token=bool(cfg["bridge_token"])
        )
    except Exception as exc:
        _emit({"ok": False, "problemes": [f"base inutilisable: {exc!r}"]}, args)
        return 1
    try:
        sante = svc.health()
        detail.update(sante)
        if not sante.get("ok"):
            problemes.append("la base ne repond pas correctement")
        depuis = sante.get("derniere_observation_il_y_a_s")
        if depuis is None:
            problemes.append("aucune observation n'a jamais ete recue : le pont est-il installe ?")
        elif depuis > 86400:
            problemes.append(
                f"aucune observation depuis {round(depuis / 3600)} h — canari : le pont est "
                "probablement tombe a une mise a jour de Hermes"
            )
        if cfg.patterns and not Path(cfg.patterns).is_file():
            problemes.append(f"motifs absents ({cfg.patterns}), les valeurs par defaut sont utilisees")
        if not cfg["bridge_token"]:
            problemes.append(
                "aucun jeton : tout appel sur /mcp/bridge est accepte, la seule protection "
                "etant le binding sur la boucle locale"
            )
        # Le catalogue remplace le balayage du disque. GK n'a pas le volume de
        # l'agent monte -- c'est la conception par push -- et `racine_skills`
        # laissait croire le contraire : il annoncait une racine illisible comme
        # un probleme alors que c'est l'etat normal.
        cat = detail.get("catalogue") or {}
        if cat.get("skills", 0) == 0:
            problemes.append(
                "catalogue vide : aucun manifeste recu, GK ne connait aucun skill"
            )
        elif cat.get("manifeste_perime"):
            problemes.append(
                f"catalogue perime : dernier manifeste il y a "
                f"{round((cat.get('dernier_manifeste_il_y_a_s') or 0) / 3600)} h"
            )
    finally:
        svc.close()
    detail["ok"] = not problemes
    detail["problemes"] = problemes
    _emit(detail, args)
    return 0 if not problemes else 1


def cmd_forget(args, cfg: Config) -> int:
    """Efface du texte sans perdre la mesure. C'est le decoupage en trois
    niveaux qui le rend possible."""
    with Store(cfg.database) as store:
        done: dict[str, int] = {}
        if args.extracts:
            cur = store.execute(
                "UPDATE corrections SET extract = NULL, extract_expires_at = NULL "
                "WHERE extract IS NOT NULL"
            )
            done["preuves_effacees"] = cur.rowcount or 0
        if args.demandes:
            # Le contenu metier des demandes reelles, efface sans toucher au
            # compteur : la mesure survit a l'effacement du texte.
            cur = store.execute("DELETE FROM requests")
            done["demandes_supprimees"] = cur.rowcount or 0
        if args.before:
            cutoff = _parse_date(args.before)
            cur = store.execute(
                "UPDATE corrections SET extract = NULL, extract_expires_at = NULL, "
                "request_truncated = NULL, testcase_retired_at = COALESCE(testcase_retired_at, ?) "
                "WHERE ts < ?",
                (now(), cutoff),
            )
            done["corrections_anonymisees"] = cur.rowcount or 0
            cur = store.execute("DELETE FROM briefs WHERE ts < ?", (cutoff,))
            done["briefs_supprimes"] = cur.rowcount or 0
            cur = store.execute("DELETE FROM requests WHERE ts < ?", (cutoff,))
            done["demandes_supprimees"] = cur.rowcount or 0
        if args.conv:
            cur = store.execute(
                "UPDATE corrections SET extract = NULL, extract_expires_at = NULL, "
                "request_truncated = NULL WHERE session_id = ?",
                (args.conv,),
            )
            done["corrections_anonymisees"] = (
                done.get("corrections_anonymisees", 0) + (cur.rowcount or 0)
            )
            cur = store.execute("DELETE FROM briefs WHERE session_id = ?", (args.conv,))
            done["briefs_supprimes"] = done.get("briefs_supprimes", 0) + (cur.rowcount or 0)
            cur = store.execute("DELETE FROM requests WHERE session_id = ?", (args.conv,))
            done["demandes_supprimees"] = (
                done.get("demandes_supprimees", 0) + (cur.rowcount or 0)
            )
        if not done:
            _emit({
                "ok": False,
                "erreur": "precisez --extracts, --demandes, --before ou --conv",
            }, args)
            return 2
        done["note"] = "les compteurs sont intacts : la mesure n'est pas perdue"
        _emit(done, args)
    return 0


def cmd_export(args, cfg: Config) -> int:
    with Store(cfg.database) as store:
        dump = {
            "exporte_le": _iso(now()),
            "schema": store.migrate(),
            "tables": {
                name: [dict(r) for r in store.query(f"SELECT * FROM {name}")]
                for name in IRREMPLACABLE
            },
        }
    text = json.dumps(dump, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        _emit({"ok": True, "fichier": args.output, "octets": len(text)}, args)
    else:
        sys.stdout.write(text + "\n")
    return 0


def cmd_tidy(args, cfg: Config) -> int:
    with Store(cfg.database) as store:
        result = tidy(store)
        # Les skills vivants viennent du catalogue, non d'un balayage du disque :
        # GK n'a plus le volume de l'agent monte, et deux sources de verite sur
        # le catalogue seraient le defaut meme qu'il doit corriger chez gb.
        #
        # `retire_testcases` refuse d'agir sur un ensemble vide, donc un
        # catalogue pas encore recu ne met pas le corpus du banc au rebut.
        vivants = Catalog(store).live_names()
        result["skills_au_catalogue"] = len(vivants)
        result["cas_de_test_retires"] = retire_testcases(store, vivants)
        if not vivants:
            result["avertissement"] = (
                "catalogue vide : aucun cas de test retire, et c'est voulu"
            )
        # Le signalement des orphelins demandait de lire le disque de l'agent.
        # Il reviendra par un champ du manifeste, le pont etant le seul a le
        # voir. Promettre moins vaut mieux que mesurer la mauvaise chose.
        _emit(result, args)
    return 0


def cmd_catalogue(args, cfg: Config) -> int:
    """L'etat du catalogue. Il n'y a rien a lancer pour le remplir : c'est le
    pont qui pousse le manifeste, au plus une fois par heure."""
    with Store(cfg.database) as store:
        catalogue = Catalog(store)
        out = dict(catalogue.stats())
        if out["skills"] == 0:
            out["remarque"] = (
                "aucun manifeste recu. GK le reclame par le canal retour "
                "(besoin `catalogue_vide`) a chaque reponse qu'il rend deja."
            )
        else:
            out["a_reclamer_au_prochain_tour"] = catalogue.wanted()
            rows = store.query(
                "SELECT category, COUNT(*) n FROM skills WHERE missing_since IS NULL "
                "GROUP BY category ORDER BY n DESC LIMIT 15"
            )
            out["par_categorie"] = {r["category"] or "(sans)": r["n"] for r in rows}
            sans_desc = store.one(
                "SELECT COUNT(*) c FROM skills WHERE missing_since IS NULL AND description = ''"
            )
            out["sans_description"] = int(sans_desc["c"]) if sans_desc else 0
        _emit(out, args)
    return 0


def cmd_skill(args, cfg: Config) -> int:
    """Les quatre champs tels que GK les a indexes, pour un skill.

    Sert a verifier qu'une section de declencheurs qu'on vient d'ecrire est lue
    comme on le croit : le decoupage positif/negatif est une devinette sur du
    texte redige par des humains, et il doit pouvoir se relire.
    """
    with Store(cfg.database) as store:
        nom = args.nom
        row = store.one(
            "SELECT * FROM skills WHERE name = ? OR path = ? OR path LIKE ? "
            "ORDER BY (name = ?) DESC LIMIT 1",
            (nom, nom, f"%{nom}%", nom),
        )
        if row is None:
            # Proposer des candidats plutot que de laisser chercher : les noms
            # reels sont longs et prefixes, et une faute de frappe ne doit pas
            # couter un `gk catalogue` complet.
            plie = fold(nom)
            candidats = [
                r["name"] for r in store.query(
                    "SELECT name FROM skills WHERE missing_since IS NULL AND name <> ''"
                )
                if plie and (plie in fold(r["name"]) or fold(r["name"]) in plie)
            ]
            if not candidats:
                # Decoupe aussi sur les tirets : les noms reels sont
                # `gm-supplier-invoice-control`, donc un mot utile est separe
                # par un tiret et non par une espace.
                morceaux = [
                    m for m in re.split(r"[^a-z0-9]+", plie) if len(m) >= 4
                ] or [plie]
                candidats = [
                    r["name"] for r in store.query(
                        "SELECT name FROM skills WHERE missing_since IS NULL AND name <> ''"
                    )
                    if any(m in fold(r["name"]) for m in morceaux)
                ]
            _emit({
                "ok": False,
                "erreur": f"aucun skill ne correspond a {nom!r}",
                "candidats": sorted(candidats)[:10],
                "remarque": None if candidats else "voir `gk catalogue` pour la liste",
            }, args)
            return 1
        sections = store.query(
            "SELECT heading, text FROM skill_sections WHERE path = ? ORDER BY heading",
            (row["path"],),
        )
        rubriques = []
        for s in sections:
            positif, negatif, marqueurs = decouper(s["heading"], s["text"] or "")
            rubriques.append({
                "titre": s["heading"],
                "positif": positif,
                "negatif": negatif,
                "marqueurs": sorted(marqueurs),
            })
        a_jour = bool(row["indexed_fingerprint"]) and (
            row["fingerprint"] == row["indexed_fingerprint"]
        )
        _emit({
            "ok": True,
            "name": row["name"],
            "path": row["path"],
            "category": row["category"],
            "description": row["description"],
            "declencheurs": rubriques,
            "a_jour": a_jour,
            "disparu": bool(row["missing_since"]),
            # Dire franchement ce qui manque : un skill sans declencheurs n'est
            # pas une anomalie, c'est le cas de 117 des 182.
            "remarque": None if rubriques else (
                "aucune section de declencheurs : l'index ne portera que le nom "
                "et la description pour ce skill"
            ),
        }, args)
    return 0


def cmd_cherche(args, cfg: Config) -> int:
    """Classe les skills pour une demande, avec le detail par champ.

    Le detail est le point de la commande : on doit voir **pourquoi** un skill
    remonte, pas seulement qu'il remonte. Un score sans son detail ne se corrige
    pas.
    """
    with Store(cfg.database) as store:
        index = Index.construire(store)
        if index.n == 0:
            _emit({"ok": False, "erreur": "catalogue vide, voir `gk catalogue`"}, args)
            return 1
        contexte = contexte_de_session(store, getattr(args, "session", "") or "")
        if contexte:
            resultats = index.chercher_avec_contexte(
                args.demande, contexte=contexte, limite=args.limite,
                sans_declencheurs=bool(args.sans_declencheurs),
            )
        else:
            resultats = index.chercher(
                args.demande, limite=args.limite,
                sans_declencheurs=bool(args.sans_declencheurs),
            )
        _emit({
            "ok": True,
            "demande": args.demande,
            "skills_indexes": index.n,
            "bras": "nom+description" if args.sans_declencheurs else "tous les champs",
            "poids": POIDS if not args.sans_declencheurs else {
                c: v for c, v in POIDS.items() if c in ("name", "description")
            },
            "contexte_repris": len(contexte),
            "resultats": resultats,
            "remarque": None if resultats else (
                "aucun skill ne porte ces mots : reformulez, ou c'est un trou "
                "reel de la bibliotheque"
            ),
        }, args)
    return 0


def cmd_banc_contexte(args, cfg: Config) -> int:
    """Mesure l'apport du contexte de session, sur des sessions synthetiques.

    Le corpus curate est fait de phrases isolees, donc le contexte ne pouvait
    pas etre mesure. On en fabrique une conversation : pour un skill portant au
    moins deux citations, l'une sert de tour precedent et l'autre de demande.

    Deux conditions, et la seconde compte autant : `suite`, ou le tour precedent
    porte sur le meme skill et le contexte devrait aider ; `bascule`, ou il
    porte sur un autre et le contexte devrait nuire. Un mecanisme qui aide la
    suite en cassant la bascule n'est pas un gain, c'est un pari sur la
    stabilite des conversations.
    """
    with Store(cfg.database) as store:
        index = Index.construire(store)
        cas = index.corpus_avec_contexte()
        if not cas:
            _emit({
                "ok": False,
                "erreur": "aucun skill ne porte deux citations : pas de session "
                          "synthetique possible",
                "suite": "voir `gk declencheurs`",
            }, args)
            return 1
        rangs: dict[str, dict[str, list]] = {}
        for cas_un in cas:
            for bras in ("sans", "avec"):
                if bras == "sans":
                    sortie = index.chercher(
                        cas_un["demande"], limite=args.limite,
                        exclure_exemple=cas_un["ligne"],
                    )
                else:
                    sortie = index.chercher_avec_contexte(
                        cas_un["demande"], contexte=cas_un["contexte"],
                        poids_contexte=args.poids, limite=args.limite,
                        exclure_exemple=cas_un["ligne"],
                    )
                noms = [r["name"] for r in sortie]
                rang = (
                    noms.index(cas_un["attendu"]) + 1
                    if cas_un["attendu"] in noms else None
                )
                rangs.setdefault(cas_un["condition"], {}).setdefault(bras, []).append(rang)
        out: dict = {
            "ok": True, "cas": len(cas), "skills_indexes": index.n,
            "poids_contexte": args.poids, "rang_maximal_compte": args.limite,
        }
        for condition, bras in sorted(rangs.items()):
            out[condition] = {
                "cas": len(bras["sans"]),
                "mrr_sans_contexte": _mrr(bras["sans"]),
                "mrr_avec_contexte": _mrr(bras["avec"]),
                "rappel_1_sans": _taux(bras["sans"], 1),
                "rappel_1_avec": _taux(bras["avec"], 1),
            }
        out["lecture"] = _lire_contexte(out)
        _emit(out, args)
    return 0


def _lire_contexte(out: dict) -> list[str]:
    lignes = [
        "Sessions synthetiques : une citation sert de tour precedent, une autre "
        "de demande. Non circulaire, mais une conversation fabriquee n'est pas "
        "une vraie -- le chiffre indique un sens, pas une amplitude."
    ]
    suite, bascule = out.get("suite"), out.get("bascule")
    if suite:
        gain = round(suite["mrr_avec_contexte"] - suite["mrr_sans_contexte"], 3)
        lignes.append(
            f"Suite (meme skill) : {gain:+} de MRR avec le contexte "
            f"({suite['mrr_sans_contexte']} -> {suite['mrr_avec_contexte']})."
        )
    if bascule:
        perte = round(bascule["mrr_avec_contexte"] - bascule["mrr_sans_contexte"], 3)
        lignes.append(
            f"Bascule (autre skill) : {perte:+} de MRR "
            f"({bascule['mrr_sans_contexte']} -> {bascule['mrr_avec_contexte']})."
        )
    if suite and bascule:
        gain = suite["mrr_avec_contexte"] - suite["mrr_sans_contexte"]
        perte = bascule["mrr_sans_contexte"] - bascule["mrr_avec_contexte"]
        if gain <= 0.01:
            lignes.append(
                "=> Le contexte n'apporte rien, meme quand la conversation reste "
                "sur le sujet. Inutile de l'activer."
            )
        elif perte > gain:
            lignes.append(
                f"=> Le contexte coute plus sur la bascule ({perte:.3f}) qu'il "
                f"ne rapporte sur la suite ({gain:.3f}). Il ne vaut la peine que "
                "si les conversations changent rarement de sujet -- a mesurer "
                "sur les demandes reelles avant d'activer."
            )
        else:
            lignes.append(
                f"=> Le contexte rapporte plus ({gain:.3f}) qu'il ne coute "
                f"({perte:.3f}). Reste a verifier le taux de bascule reel : "
                "c'est lui qui decide du solde."
            )
    return lignes


def cmd_banc(args, cfg: Config) -> int:
    """Mesure la recherche sur les phrases citees des skills.

    Le corpus est fait des lignes entre guillemets des declencheurs : le bon
    skill est connu **par construction**, puisque c'est celui qui porte la
    citation. Chaque phrase est retiree de l'index de son propre skill avant
    d'etre cherchee, sans quoi la mesure serait circulaire.

    Deux bras : tous les champs, et nom + description seulement. C'est la
    question posee par GM, et elle recoit ici un chiffre.
    """
    with Store(cfg.database) as store:
        index = Index.construire(store)
        cas = index.corpus()
        if not cas:
            _emit({
                "ok": False,
                "erreur": "aucune phrase citee dans les declencheurs",
                "suite": "voir `gk declencheurs`",
            }, args)
            return 1
        # Les deux populations sont deux problemes de recherche differents :
        # les skills metier (categorie `greenmusic`) et les skills generiques
        # herites. Une moyenne des deux ne decrit ni l'un ni l'autre, et les cinq
        # premiers echecs du relevé du 2026-10-22 etaient TOUS generiques.
        categorie = {s.name: s.category for s in index.skills}
        # Et le seuil d'information : une demande dont la somme des IDF est
        # faible ne porte aucun mot de domaine. L'ecarter du jugement serait
        # malhonnete, la noyer dans la moyenne serait trompeur : on la compte a
        # part.
        informations = [index.information(d) for d, _, _ in cas]
        informations_triees = sorted(informations)
        seuil = informations_triees[max(0, len(informations_triees) // 10)]
        # Trois bras : tous les champs, nom+description seul, et citations
        # sans la prose. Le troisieme teste l'hypothese que la prose des
        # declencheurs nuit -- c'est la que vivent les renvois vers les voisins.
        #
        # Un bras precedent ponderait PAR POPULATION. Il est retire : il rendait
        # les scores incomparables, un skill note sur quatre champs battant
        # mecaniquement un skill note sur deux. Il avantageait donc le metier au
        # lieu d'ameliorer la recherche, et son gain de +0,008 etait un artefact.
        rangs: dict[str, list[int | None]] = {
            "tous": [], "sans": [], "citations": [], "normalise": [],
        }
        groupes: dict[str, dict[str, list]] = {}
        echecs: list[dict] = []
        # Deux echecs tres differents, qu'il ne faut pas confondre : aucun
        # skill ne remonte, ou un autre gagne. Le premier dit que les mots de la
        # phrase n'existent nulle part ailleurs dans le skill -- il n'est donc
        # trouvable que par cette phrase exacte, ce qui est une remarque sur la
        # redaction. Le second dit que le classement se trompe, ce qui est une
        # remarque sur le score.
        aucun = mauvais = 0
        # La decomposition des echecs par REMEDE. "88 echecs" est un tas
        # indifferencie, et les remedes sont opposes : la redaction d'un cote,
        # le score de l'autre. Ce decoupage borne la valeur du reglage.
        causes: Counter = Counter()
        par_cause: dict[str, list[dict]] = {}
        for (demande, attendu, ligne), info in zip(cas, informations):
            metier = categorie.get(attendu) == "greenmusic"
            etiquettes = [
                "metier" if metier else "generique",
                "peu_informative" if info <= seuil else "informative",
            ]
            for bras, sans in (
                ("tous", False), ("sans", True),
                ("citations", False), ("normalise", False),
            ):
                sortie = index.chercher(
                    demande, limite=args.limite, sans_declencheurs=sans,
                    exclure_exemple=ligne,
                    sans_prose=(bras == "citations"),
                    normaliser_longueur=(bras == "normalise"),
                )
                noms = [r["name"] for r in sortie]
                rang = noms.index(attendu) + 1 if attendu in noms else None
                rangs[bras].append(rang)
                for etiquette in etiquettes:
                    groupes.setdefault(etiquette, {}).setdefault(bras, []).append(rang)
                if bras == "tous" and rang is None:
                    if noms:
                        mauvais += 1
                    else:
                        aucun += 1
                    diag = index.diagnostic(
                        demande, attendu, exclure_exemple=ligne, limite=args.limite
                    )
                    cause = diag.get("cause") or "indetermine"
                    causes[cause] += 1
                    exemples = par_cause.setdefault(cause, [])
                    if len(exemples) < 3:
                        exemples.append({
                            "demande": demande, "attendu": attendu, **{
                                c: v for c, v in diag.items() if c != "cause"
                            },
                        })
                    if len(echecs) < args.echecs:
                        echecs.append({
                            "demande": demande, "attendu": attendu,
                            "remontes": noms[:3],
                            "cause": "aucun resultat" if not noms else "mauvais skill",
                        })
        out = {"ok": True, "cas": len(cas), "skills_indexes": index.n,
               "rang_maximal_compte": args.limite}
        for bras, etiquette in (
            ("tous", "tous_les_champs"),
            ("sans", "nom_description"),
            ("citations", "citations_sans_prose"),
            ("normalise", "normalise_par_longueur"),
        ):
            trouves = [r for r in rangs[bras] if r is not None]
            out[etiquette] = {
                "rappel_1": round(sum(1 for r in trouves if r == 1) / len(cas), 3),
                f"rappel_{args.limite}": round(len(trouves) / len(cas), 3),
                "mrr": round(sum(1.0 / r for r in trouves) / len(cas), 3),
            }
        out["par_population"] = {
            etiquette: {
                "cas": len(bras_rangs["tous"]),
                "rappel_1": _taux(bras_rangs["tous"], 1),
                f"rappel_{args.limite}": _taux(bras_rangs["tous"], args.limite),
                "mrr": _mrr(bras_rangs["tous"]),
                "mrr_nom_description": _mrr(bras_rangs["sans"]),
                "mrr_citations_sans_prose": _mrr(bras_rangs["citations"]),
                "mrr_normalise": _mrr(bras_rangs["normalise"]),
            }
            for etiquette, bras_rangs in sorted(groupes.items())
        }
        out["seuil_information"] = round(seuil, 2)
        gain = out["tous_les_champs"]["mrr"] - out["nom_description"]["mrr"]
        out["gain_mrr_des_declencheurs"] = round(gain, 3)
        out["echecs_sans_aucun_resultat"] = aucun
        out["echecs_mauvais_skill"] = mauvais
        out["echecs_par_cause"] = dict(causes.most_common())
        out["echecs_par_remede"] = {
            "redaction": causes["texte_absent"] + causes["texte_domine"],
            "score": causes["ordre"],
        }
        out["exemples_par_cause"] = par_cause
        out["lecture"] = _lire_banc(out, gain)
        out["echecs"] = echecs
        _emit(out, args)
    return 0


def _taux(rangs: list, limite: int) -> float:
    if not rangs:
        return 0.0
    return round(
        sum(1 for r in rangs if r is not None and r <= limite) / len(rangs), 3
    )


def _mrr(rangs: list) -> float:
    if not rangs:
        return 0.0
    return round(sum(1.0 / r for r in rangs if r is not None) / len(rangs), 3)


def _lire_banc(out: dict, gain: float) -> list[str]:
    lignes = [
        "Corpus curate : une citation est plus propre qu'une demande reelle, "
        "donc ces chiffres sont optimistes par rapport a la production."
    ]
    if gain <= 0.01:
        lignes.append(
            "=> Les declencheurs n'apportent rien de mesurable. Nom et "
            "description suffisent, et l'index peut se reduire a deux champs."
        )
    elif gain < 0.05:
        lignes.append(
            "=> Apport faible. A garder puisqu'il est gratuit, mais ne pas "
            "fonder la justification de GK dessus."
        )
    else:
        lignes.append(
            "=> Les declencheurs apportent nettement. C'est le travail de "
            "redaction de la bibliotheque qui paie, pas le moteur."
        )
    pop = out.get("par_population") or {}
    metier, generique = pop.get("metier"), pop.get("generique")
    if metier and generique:
        lignes.append(
            f"Metier {metier['mrr']} de MRR sur {metier['cas']} cas, generique "
            f"{generique['mrr']} sur {generique['cas']}. Ce sont deux problemes "
            "de recherche differents, et la moyenne ne decrit ni l'un ni l'autre."
        )
    # Un signe oppose selon la population est l'information la plus utile du
    # banc, et une moyenne la ferait disparaitre.
    for nom, bloc in (("metier", metier), ("generique", generique)):
        if not bloc:
            continue
        ecart = round(bloc["mrr"] - bloc["mrr_nom_description"], 3)
        if ecart < 0:
            lignes.append(
                f"INVERSION sur {nom} : nom+description font MIEUX que tous les "
                f"champs ({bloc['mrr_nom_description']} contre {bloc['mrr']}, "
                f"{ecart}). Les declencheurs NUISENT a cette population."
            )
    citations = out.get("citations_sans_prose")
    if citations:
        ecart = round(citations["mrr"] - out["tous_les_champs"]["mrr"], 3)
        if ecart > 0.01:
            lignes.append(
                f"Les citations SANS la prose font {citations['mrr']} contre "
                f"{out['tous_les_champs']['mrr']} ({ecart:+}). La prose des "
                "declencheurs nuit : c'est la que vivent les renvois vers les "
                "skills voisins. Retirer le champ `positif` est un changement de "
                "poids, donc gratuit."
            )
        elif ecart < -0.01:
            lignes.append(
                f"La prose des declencheurs apporte ({citations['mrr']} sans elle "
                f"contre {out['tous_les_champs']['mrr']} avec) : la garder."
            )
    norm = out.get("normalise_par_longueur")
    if norm:
        ecart = round(norm["mrr"] - out["tous_les_champs"]["mrr"], 3)
        gen = (pop.get("generique") or {}).get("mrr_normalise")
        gen_sans = (pop.get("generique") or {}).get("mrr_nom_description")
        if ecart > 0.01:
            lignes.append(
                f"La normalisation par la longueur apporte {ecart:+} "
                f"({norm['mrr']} contre {out['tous_les_champs']['mrr']}). Un "
                "champ long rencontrait plus de demandes par hasard : c'est la "
                "partie de BM25 qui avait ete jugee inutile ici."
            )
            if gen is not None and gen_sans is not None and gen >= gen_sans:
                lignes.append(
                    "Et elle resorbe l'inversion du generique, sans aucune regle "
                    "par population : le defaut etait la longueur, pas la "
                    "categorie."
                )
        elif ecart < -0.01:
            lignes.append(
                f"La normalisation par la longueur nuit ({ecart}) : les champs "
                "longs portaient donc du signal, pas du bruit."
            )
    peu = pop.get("peu_informative")
    if peu:
        lignes.append(
            f"{peu['cas']} demandes portent peu de mots de domaine : MRR "
            f"{peu['mrr']} contre {pop.get('informative', {}).get('mrr', '?')}. "
            "Elles sont plus dures, pas introuvables -- une premiere version de "
            "cette lecture affirmait le contraire, et les donnees la "
            "contredisaient."
        )
    remede = out.get("echecs_par_remede") or {}
    red, sco = remede.get("redaction", 0), remede.get("score", 0)
    if red or sco:
        total_echecs = red + sco
        part = 100.0 * red / total_echecs if total_echecs else 0.0
        lignes.append(
            f"Sur {total_echecs} echecs : {red} relevent de la REDACTION "
            f"({part:.0f} %) et {sco} du SCORE. Un echec de redaction est un "
            "skill dont les mots de la demande sont absents, ou domines par ceux "
            "d'un concurrent -- aucun coefficient n'y changera rien."
        )
        if part >= 60:
            lignes.append(
                "=> Regler les coefficients ne peut corriger qu'une minorite des "
                "echecs. Le plafond est dans la bibliotheque, pas dans le moteur."
            )
        elif part <= 30:
            lignes.append(
                "=> La majorite des echecs est un probleme de classement : le "
                "reglage du score a de la marge."
            )
    aucun = out.get("echecs_sans_aucun_resultat", 0)
    mauvais = out.get("echecs_mauvais_skill", 0)
    if aucun or mauvais:
        lignes.append(
            f"Echecs : {aucun} sans aucun resultat, {mauvais} ou un autre skill "
            "gagne. Les premiers sont une remarque sur la REDACTION -- les mots "
            "de la phrase n'existent nulle part ailleurs dans le skill, qui "
            "n'est donc trouvable que par cette phrase exacte. Les seconds sont "
            "une remarque sur le SCORE."
        )
    return lignes


def cmd_manques(args, cfg: Config) -> int:
    """La liste de ce qu'il faut ecrire, groupee par skill.

    67 % des echecs du banc relevent de la redaction (releve du 2026-10-23) :
    48 cas ou le skill attendu ne porte AUCUN mot de la demande, 12 ou un
    concurrent porte les siens et plus. Aucun coefficient n'y changera rien, et
    le travail utile est une liste de courses -- pas une statistique.
    """
    with Store(cfg.database) as store:
        index = Index.construire(store)
        if index.n == 0:
            _emit({"ok": False, "erreur": "catalogue vide, voir `gk catalogue`"}, args)
            return 1
        cas = index.corpus()
        if not cas:
            _emit({
                "ok": False,
                "erreur": "aucune phrase citee dans les declencheurs",
                "suite": "voir `gk declencheurs`",
            }, args)
            return 1
        manques = index.manques(cas)
        _emit({
            "ok": True,
            "cas_examines": len(cas),
            "skills_a_enrichir": len(manques),
            "detail": manques[: max(1, args.limite)],
            "lecture": [
                "Pour chaque skill : les demandes qu'il devrait capter, et les "
                "mots qui MANQUENT a son texte.",
                "Ajouter ces mots a sa description ou a ses declencheurs est le "
                "seul levier qui puisse monter le rappel en premiere position : "
                "le reglage des coefficients ne touche qu'un tiers des echecs.",
                "Un mot deja present ailleurs dans la bibliotheque rapportera "
                "moins qu'un mot propre au skill : c'est l'IDF qui decide.",
            ],
        }, args)
    return 0


def cmd_confusions(args, cfg: Config) -> int:
    """Les paires de skills trop proches pour etre departagees.

    Ce n'est pas un defaut de GK : si deux textes ne se distinguent pas, aucun
    index ne les distinguera. C'est un defaut de la bibliotheque, et la moitie
    negative des declencheurs est le mecanisme prevu pour le corriger.
    """
    with Store(cfg.database) as store:
        index = Index.construire(store)
        if index.n == 0:
            _emit({"ok": False, "erreur": "catalogue vide, voir `gk catalogue`"}, args)
            return 1
        paires = index.confusions(seuil=args.seuil)
        sans_interdiction = [p for p in paires if not p["interdiction"]]
        doublons = index.doublons_de_nom()
        lecture = []
        if doublons:
            lecture.append(
                f"{len(doublons)} skills portent le MEME nom a deux chemins "
                "differents. Ce n'est pas une paire confuse, c'est un doublon : "
                "Hermes livre une version groupee, la version locale est gardee, "
                "et les deux coexistent. prompt_builder deduplique par nom donc "
                "gb n'en voit qu'un, mais GK indexe les deux -- ils se "
                "concurrencent et se partagent l'IDF, ce qui abaisse leur score "
                "a tous les deux."
            )
        if paires:
            lecture += [
                "La moitie negative des declencheurs EST le mecanisme de "
                "desambiguisation prevu par la conception.",
                f"{len(sans_interdiction)} paires sur {len(paires)} n'ont "
                "d'interdiction d'aucun cote : le mecanisme existe et n'a pas "
                "ete utilise. C'est la que l'ecriture rapporte le plus.",
            ]
        else:
            lecture.append(
                f"Aucune paire au-dela de {args.seuil} de proximite. Abaisser "
                "--seuil pour voir les paires moins proches : `baoyu-comic` et "
                "`baoyu-infographic` se confondent au banc sans atteindre 0,6."
            )
        _emit({
            "ok": True,
            "skills_indexes": index.n,
            "seuil": args.seuil,
            "doublons_de_nom": doublons,
            "paires": len(paires),
            "paires_sans_aucune_interdiction": len(sans_interdiction),
            "detail": paires[: max(1, args.limite)],
            "lecture": lecture,
        }, args)
    return 0


def cmd_demandes(args, cfg: Config) -> int:
    """L'etat des demandes reelles, et le skill charge dans la foulee.

    Le rapprochement se fait par session et par proximite dans le temps, faute
    de clef commune : `pre_llm_call` ne porte pas de `turn_id` exploitable et
    `on_skill_lifecycle` n'en porte pas du tout. Ce n'est donc pas une verite,
    c'est une correlation -- et le dire importe, parce qu'un corpus etiquete par
    correlation ne vaut pas un corpus etiquete par construction.
    """
    with Store(cfg.database) as store:
        total = int(store.counter("requests_total"))
        gardees = int(store.one("SELECT COUNT(*) c FROM requests")["c"])
        dernier = store.counter("last_request_ts")
        out: dict = {
            "demandes_observees": total,
            "demandes_gardees": gardees,
            "derniere_il_y_a_s": round(now() - dernier, 1) if dernier else None,
        }
        if gardees == 0:
            out["remarque"] = (
                "aucune demande observee. Le pont les transmet depuis "
                "`pre_llm_call` : si ce chiffre reste a zero apres un message, "
                "voir `demande_introuvable` dans /opt/data/gk-bridge.jsonl"
            )
            _emit(out, args)
            return 0
        out["par_plateforme"] = {
            (r["platform"] or "(inconnue)"): r["n"]
            for r in store.query(
                "SELECT platform, COUNT(*) n FROM requests GROUP BY platform "
                "ORDER BY n DESC LIMIT 10"
            )
        }
        premiers = store.one(
            "SELECT COUNT(*) c FROM requests WHERE is_first_turn = 1"
        )
        out["premiers_tours"] = int(premiers["c"]) if premiers else 0
        longueurs = [
            len(r["text_truncated"] or "")
            for r in store.query("SELECT text_truncated FROM requests")
        ]
        if longueurs:
            longueurs.sort()
            out["longueur_medianne"] = longueurs[len(longueurs) // 2]
            out["tronquees_a_200"] = sum(1 for n in longueurs if n >= 200)
        # Deux rapprochements, et c'est le second qui porte la question.
        #
        # Le premier regarde les skills charges dans les deux minutes qui
        # suivent la demande. Il ne vaut que pour un premier tour : au dixieme
        # message d'une conversation, le skill charge au debut sert encore, et
        # cette fenetre ne le voit pas. Un premier releve a donne 0 sur 6
        # demandes, toutes en milieu de conversation -- un artefact du
        # fenetrage, qu'il aurait ete facile de lire comme un resultat.
        apparies = store.one(
            "SELECT COUNT(*) c FROM requests r WHERE EXISTS ("
            "  SELECT 1 FROM skill_uses u WHERE u.session_id = r.session_id "
            "  AND u.ts BETWEEN r.ts AND r.ts + 120)"
        )
        out["demandes_suivies_d_un_skill_sous_2min"] = (
            int(apparies["c"]) if apparies else 0
        )
        # Le second regarde la SESSION entiere : cette conversation a-t-elle
        # charge un skill, a un moment quelconque ? C'est la vraie forme de la
        # question « gb repond-il sans rien charger, malgre le « you MUST load
        # it » de son prompt systeme ? »
        sessions = {
            r["session_id"] for r in store.query(
                "SELECT DISTINCT session_id FROM requests WHERE session_id <> ''"
            )
        }
        avec_skill = {
            r["session_id"] for r in store.query(
                "SELECT DISTINCT session_id FROM skill_uses WHERE session_id <> ''"
            )
        }
        out["sessions_observees"] = len(sessions)
        out["sessions_sans_aucun_skill"] = len(sessions - avec_skill)
        sans = store.one(
            "SELECT COUNT(*) c FROM requests r WHERE NOT EXISTS ("
            "  SELECT 1 FROM skill_uses u WHERE u.session_id = r.session_id)"
        )
        out["demandes_dans_une_session_sans_skill"] = int(sans["c"]) if sans else 0
        # Le taux de bascule reel : c'est lui qui decide du solde du contexte.
        # Seuil de rentabilite mesure au banc : 11 % de tours restant sur le
        # sujet suffisent a ce que le contexte soit gagnant.
        distincts = store.query(
            "SELECT session_id, COUNT(DISTINCT skill_name) n FROM skill_uses "
            "WHERE session_id <> '' GROUP BY session_id"
        )
        if distincts:
            un_seul = sum(1 for r in distincts if r["n"] == 1)
            out["sessions_avec_skills"] = len(distincts)
            out["sessions_a_un_seul_skill"] = un_seul
            out["taux_de_suite_estime"] = round(un_seul / len(distincts), 3)
            out["remarque_bascule"] = (
                "une session qui ne charge qu'un seul skill distinct est une "
                "conversation restee sur le sujet. Le contexte est gagnant des "
                "11 % : au-dela, il rapporte plus qu'il ne coute"
            )
        out["remarque_appariement"] = (
            "deux mesures : la fenetre de deux minutes ne vaut que pour un "
            "premier tour, et c'est le compte par SESSION qui porte la question. "
            "Dans les deux cas c'est une correlation et non une verite, faute de "
            "clef commune entre pre_llm_call et on_skill_lifecycle"
        )
        if out["sessions_observees"] and out["sessions_sans_aucun_skill"] == out[
            "sessions_observees"
        ]:
            out["remarque_charge"] = (
                "aucune session observee n'a charge de skill. A confirmer sur du "
                "volume : six conversations ne disent rien, et des messages "
                "triviaux n'ont aucune raison d'en charger un."
            )
        if args.exemples:
            out["exemples"] = [
                r["text_truncated"] for r in store.query(
                    "SELECT text_truncated FROM requests ORDER BY ts DESC LIMIT ?",
                    (max(1, min(args.exemples, 50)),),
                )
            ]
        _emit(out, args)
    return 0


def cmd_reindexer(args, cfg: Config) -> int:
    """Oublie ce qui a ete indexe, pour que le pont renvoie TOUTES les tranches.

    A lancer apres tout changement d'une regle de lecture : les empreintes des
    fichiers n'ayant pas bouge, GK ne redemanderait rien de lui-meme.
    """
    with Store(cfg.database) as store:
        catalogue = Catalog(store)
        touches = catalogue.reindexer()
        _emit({
            "ok": True,
            "skills_a_relire": touches,
            "suite": "envoyez un message a l'agent : la relecture part dans la "
                     "minute qui suit sa reponse, par paquets de 40",
        }, args)
    return 0


def cmd_rafraichir(args, cfg: Config) -> int:
    """Demande un manifeste au prochain message de l'agent.

    GK ne parle jamais le premier : il pose un drapeau que le prochain `observe`
    traduit en besoin `tranches_attendues`, que le pont honore avec un plancher
    d'une minute. Donc apres une edition de SKILL.md, un message a gb suffit a
    la faire prendre en compte, au lieu d'attendre la ronde horaire.
    """
    with Store(cfg.database) as store:
        catalogue = Catalog(store)
        catalogue.demander_manifeste()
        _emit({
            "ok": True,
            "demande": True,
            "suite": "envoyez un message a l'agent : la ronde partira dans la "
                     "minute qui suit sa reponse",
        }, args)
    return 0


def cmd_declencheurs(args, cfg: Config) -> int:
    """Mesure avant de trancher : la colonne a poids negatif ne vaut que si les
    skills declarent reellement quand NE PAS les charger. Lecture seule."""
    with Store(cfg.database) as store:
        mesure = mesurer(store, exemples=bool(getattr(args, "exemples", False)))
        if mesure["skills_avec_declencheurs"] == 0:
            mesure["remarque"] = (
                "aucune section de declencheurs recue. Le pont les pousse avec le "
                "manifeste : voir `gk catalogue`."
            )
        else:
            mesure["lecture"] = lecture(mesure)
        _emit(mesure, args)
    return 0


def cmd_patterns(args, cfg: Config) -> int:
    d = Detector.load(cfg.patterns)
    _emit(
        {
            "source": d.source,
            "motifs": len(d.raw_patterns),
            "empreinte": d.digest,
            "liste": d.raw_patterns if args.verbose else None,
        },
        args,
    )
    return 0


# -- utilitaires -----------------------------------------------------------


def _iso(ts: float | None) -> str | None:
    import datetime as dt

    if not ts:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def _parse_date(text: str) -> float:
    import datetime as dt

    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"date illisible: {text!r} (attendu AAAA-MM-JJ)")


def _emit(payload: dict, args) -> None:
    payload = {k: v for k, v in payload.items() if v is not None}
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return
    for key, value in payload.items():
        if isinstance(value, (list, dict)):
            sys.stdout.write(f"{key}:\n")
            sys.stdout.write("  " + json.dumps(value, ensure_ascii=False, indent=2).replace("\n", "\n  ") + "\n")
        else:
            sys.stdout.write(f"{key}: {value}\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gk", description="GreenKnowledge")
    p.add_argument("--config", help="chemin du config.yaml")
    p.add_argument("--json", action="store_true", help="sortie JSON brute")
    sub = p.add_subparsers(dest="commande", required=True)

    sub.add_parser("stats", help="l'indicateur de corrections").set_defaults(func=cmd_stats)
    sub.add_parser("check", help="l'etat, et la detection de coupure silencieuse").set_defaults(
        func=cmd_check
    )
    sub.add_parser("tidy", help="appliquer les retentions").set_defaults(func=cmd_tidy)

    f = sub.add_parser("forget", help="effacer du texte sans perdre la mesure")
    f.add_argument("--extracts", action="store_true", help="effacer toutes les preuves")
    f.add_argument(
        "--demandes", action="store_true",
        help="effacer les demandes reelles des utilisateurs (le compteur reste)",
    )
    f.add_argument("--before", help="anonymiser avant cette date (AAAA-MM-JJ)")
    f.add_argument("--conv", help="anonymiser une conversation")
    f.set_defaults(func=cmd_forget)

    e = sub.add_parser("export", help="sauvegarder les quatre tables irremplacables")
    e.add_argument("-o", "--output", help="fichier de sortie")
    e.set_defaults(func=cmd_export)

    sub.add_parser(
        "catalogue", help="l'etat du catalogue recu par manifeste"
    ).set_defaults(func=cmd_catalogue)

    sk = sub.add_parser("skill", help="les quatre champs indexes d'un skill")
    sk.add_argument("nom", help="nom du skill, ou fragment de son chemin")
    sk.set_defaults(func=cmd_skill)

    c = sub.add_parser("cherche", help="classer les skills pour une demande")
    c.add_argument("demande", help="la demande, entre guillemets")
    c.add_argument("-n", "--limite", type=int, default=10)
    c.add_argument(
        "--sans-declencheurs", action="store_true",
        help="nom et description seulement : le bras temoin du banc",
    )
    c.add_argument(
        "--session",
        help="identifiant de session : reprend les demandes precedentes comme contexte",
    )
    c.set_defaults(func=cmd_cherche)

    bc = sub.add_parser(
        "banc-contexte",
        help="mesurer l'apport du contexte de session, sur des sessions synthetiques",
    )
    bc.add_argument("-n", "--limite", type=int, default=5)
    bc.add_argument(
        "--poids", type=float, default=POIDS_CONTEXTE,
        help="poids des jetons du tour precedent",
    )
    bc.set_defaults(func=cmd_banc_contexte)

    b = sub.add_parser(
        "banc",
        help="mesurer la recherche sur les phrases citees des skills",
    )
    b.add_argument("-n", "--limite", type=int, default=5, help="rang maximal compte")
    b.add_argument("--echecs", type=int, default=5, help="nombre d'echecs a montrer")
    b.set_defaults(func=cmd_banc)

    mq = sub.add_parser(
        "manques",
        help="ce qu'il faut ECRIRE : les mots qui manquent a chaque skill",
    )
    mq.add_argument("-n", "--limite", type=int, default=20)
    mq.set_defaults(func=cmd_manques)

    cf = sub.add_parser(
        "confusions",
        help="les paires de skills que leurs textes ne separent pas",
    )
    cf.add_argument("--seuil", type=float, default=0.6, help="proximite minimale")
    cf.add_argument("-n", "--limite", type=int, default=15)
    cf.set_defaults(func=cmd_confusions)

    dm = sub.add_parser(
        "demandes",
        help="l'etat des demandes reelles observees, et ce qu'elles ont charge",
    )
    dm.add_argument(
        "--exemples", type=int, default=0,
        help="afficher N demandes tronquees (contenu metier : a utiliser sciemment)",
    )
    dm.set_defaults(func=cmd_demandes)

    sub.add_parser(
        "reindexer",
        help="tout relire : oublie ce qui a ete indexe et reclame un manifeste",
    ).set_defaults(func=cmd_reindexer)

    sub.add_parser(
        "rafraichir",
        help="demander un manifeste au prochain message de l'agent",
    ).set_defaults(func=cmd_rafraichir)

    d = sub.add_parser(
        "declencheurs",
        help="mesure ce que le tri positif/negatif donnerait, sans rien ecrire",
    )
    d.add_argument(
        "--exemples", action="store_true",
        help="inclut quelques lignes tronquees de contre-indication",
    )
    d.set_defaults(func=cmd_declencheurs)

    pa = sub.add_parser("patterns", help="les motifs charges et leur empreinte")
    pa.add_argument("-v", "--verbose", action="store_true")
    pa.set_defaults(func=cmd_patterns)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    return int(args.func(args, cfg))


if __name__ == "__main__":
    raise SystemExit(main())
