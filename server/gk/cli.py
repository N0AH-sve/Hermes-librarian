"""La commande `gk`. Elle tourne dans le conteneur, aucun port n'etant publie
au-dela de la boucle locale.

Les commandes :

* `stats`    — l'indicateur, coupe en deux comme l'etape 2 l'exige, et qui
               **refuse de comparer** deux periodes mesurees avec des motifs
               differents ;
* `check`    — l'etat, pour le rituel de mise a jour en trois temps ;
* `forget`   — effacer le texte sans perdre la mesure ;
* `export`   — les quatre tables irremplacables, et elles seules ;
* `patterns` — les motifs charges et leur empreinte ;
* `optimize` — passer une demande dans l'optimiseur SANS rien injecter, pour
               regler les regles et le lexique sans faire un aller-retour par
               l'agent. C'est la commande qu'on utilise pour decider si une
               regle merite d'exister.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .corrections import (
    BAD_RECOMMENDATION,
    BRIEF_IGNORED,
    UNATTRIBUTABLE,
    retire_testcases,
    tidy,
)
from .optimizer import Lexique, Optimiseur, appliquer_retention, rendre
from .patterns import Detector
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
        arrets = store.query(
            "SELECT rules_digest, kind, COUNT(*) n, SUM(emitted) emis, "
            "MIN(ts) debut, MAX(ts) fin FROM optimizer_stops "
            "GROUP BY rules_digest, kind ORDER BY debut",
        )
        if arrets:
            empreintes = {a["rules_digest"] for a in arrets}
            out["optimiseur"] = {
                "lectures": int(store.counter("optimize_calls")),
                "triviales": int(store.counter("optimize_triviaux")),
                "lectures_corrigees": int(store.counter("optimize_lectures_corrigees")),
                "arrets_emis": int(store.counter("optimize_arrets")),
                "arrets_etouffes": int(store.counter("optimize_arrets_etouffes")),
                "etouffes_question_deja_posee": int(store.counter("optimize_etouffes_deja_pose")),
                "etouffes_plafond_atteint": int(store.counter("optimize_etouffes_plafond")),
                "ambiguites": int(store.counter("optimize_ambiguites")),
                "par_genre": {
                    a["kind"]: {"total": a["n"], "emis": int(a["emis"] or 0)} for a in arrets
                },
                # Meme refus que pour les motifs, et pour la meme raison :
                # ajouter une regle de contradiction au milieu d'une mesure
                # ferait monter le nombre d'arrets sans que rien n'ait change
                # dans les demandes.
                "comparaison": (
                    f"REFUSEE — {len(empreintes)} jeux de regles differents"
                    if len(empreintes) > 1
                    else "possible, un seul jeu de regles"
                ),
            }
        elif store.counter("optimize_calls"):
            out["optimiseur"] = {
                "lectures": int(store.counter("optimize_calls")),
                "arrets_emis": 0,
                "note": "aucune contradiction detectee a ce jour",
            }
        if len(groups) > 1:
            # Le refus est le point de la commande, pas une precaution : un
            # motif ajoute au milieu d'une mesure fait monter le chiffre sans
            # que rien n'ait change chez l'agent.
            out["comparaison"] = (
                f"REFUSEE — {len(groups)} jeux de motifs differents. Les periodes ne sont pas "
                "comparables entre elles ; comparez a empreinte egale."
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
            cfg.database,
            cfg.patterns,
            require_token=bool(cfg["bridge_token"]),
            rules_path=cfg.optimizer_rules,
            lexicon_path=cfg.lexicon,
            share=cfg.optimizer_share,
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
        if cfg.optimizer_rules and not Path(cfg.optimizer_rules).is_file():
            problemes.append(
                f"regles de l'optimiseur absentes ({cfg.optimizer_rules}) : seules deux "
                "contradictions par defaut sont detectees"
            )
        if cfg.lexicon and not Path(cfg.lexicon).is_file():
            problemes.append(
                f"lexique absent ({cfg.lexicon}) : aucune correction du francais, la detection "
                "des contradictions continue"
            )
        if cfg.optimizer_share and svc.optimiseur is not None:
            # Un optimiseur actif dont le canari est muet est plus inquietant
            # qu'un optimiseur inactif : il est sur le chemin d'entree.
            depuis_lecture = sante.get("optimiseur", {}).get("derniere_lecture_il_y_a_s")
            if depuis_lecture is None:
                problemes.append(
                    "l'optimiseur est actif mais n'a jamais rien lu : le plugin gk-optimizer "
                    "est-il installe, et `mcp_allowlist` lui donne-t-il acces ?"
                )
            elif depuis_lecture > 86400:
                problemes.append(
                    f"aucune lecture depuis {round(depuis_lecture / 3600)} h alors que "
                    "l'optimiseur est actif — canari : le hook pre_llm_call a-t-il ete renomme ?"
                )
        if not cfg["bridge_token"]:
            problemes.append(
                "aucun jeton : tout appel sur /mcp/bridge est accepte, la seule protection "
                "etant le binding sur la boucle locale"
            )
        skills = Path(cfg["skills_root"])
        detail["racine_skills"] = str(skills)
        if not skills.is_dir():
            detail["racine_skills_lisible"] = False
        else:
            detail["racine_skills_lisible"] = True
            detail["skills_trouves"] = sum(1 for _ in skills.rglob("SKILL.md"))
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
            cur = store.execute(
                "UPDATE optimizer_stops SET extract = NULL, question = NULL "
                "WHERE extract IS NOT NULL OR question IS NOT NULL"
            )
            done["arrets_anonymises"] = cur.rowcount or 0
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
            cur = store.execute("DELETE FROM optimizer_constraints WHERE ts < ?", (cutoff,))
            done["consignes_supprimees"] = cur.rowcount or 0
            cur = store.execute(
                "UPDATE optimizer_stops SET extract = NULL, question = NULL WHERE ts < ?",
                (cutoff,),
            )
            done["arrets_anonymises"] = cur.rowcount or 0
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
            cur = store.execute(
                "DELETE FROM optimizer_constraints WHERE session_id = ?", (args.conv,)
            )
            done["consignes_supprimees"] = done.get("consignes_supprimees", 0) + (cur.rowcount or 0)
            cur = store.execute(
                "UPDATE optimizer_stops SET extract = NULL, question = NULL WHERE session_id = ?",
                (args.conv,),
            )
            done["arrets_anonymises"] = done.get("arrets_anonymises", 0) + (cur.rowcount or 0)
        if not done:
            _emit({"ok": False, "erreur": "precisez --extracts, --before ou --conv"}, args)
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
        result.update(appliquer_retention(store))
        root = Path(cfg["skills_root"])
        if root.is_dir():
            live = {p.parent.name for p in root.rglob("SKILL.md")}
            result["cas_de_test_retires"] = retire_testcases(store, live)
            # GK est bien place pour signaler les orphelins : 866 SKILL.md
            # existent dans le volume pour une bibliotheque de 537.
            whole = Path("/opt/data")
            if whole.is_dir():
                try:
                    total = sum(1 for _ in whole.rglob("SKILL.md"))
                    result["skill_md_hors_bibliotheque"] = max(
                        0, total - sum(1 for _ in root.rglob("SKILL.md"))
                    )
                except OSError:
                    pass
        _emit(result, args)
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


def cmd_optimize(args, cfg: Config) -> int:
    """Passe une demande dans l'optimiseur sans rien injecter ni rien ecrire.

    Elle existe parce qu'une regle de contradiction ne se juge pas dans
    l'abstrait : on lui donne la phrase qui a pose probleme, et on regarde si
    elle mord. C'est aussi le seul moyen d'exercer l'optimiseur avec
    `optimizer_share` a zero, donc sur l'installation par defaut.
    """
    from .coherence import Consigne, charger_coherence
    from .regles import charger

    sections, source_regles = charger(cfg.optimizer_rules)
    triviaux = sections.get("triviaux") if isinstance(sections.get("triviaux"), list) else []
    lexique = Lexique.load(cfg.lexicon)
    optimiseur = Optimiseur(lexique, [m for m in triviaux if isinstance(m, str)])
    coherence = charger_coherence(cfg.optimizer_rules)

    lecture = optimiseur.lire(args.texte)
    consignes: list[Consigne] = []
    if args.contexte:
        # Le contexte se simule en ligne de commande : les consignes du tour
        # anterieur sont extraites du message passe en --contexte, exactement
        # comme le service les aurait retenues.
        consignes = coherence.consignes(optimiseur.lire(args.contexte).texte)
    verdict = coherence.examiner(lecture.texte, consignes)
    _emit(
        {
            "regles": source_regles,
            "regles_empreinte": coherence.digest,
            "lexique": lexique.source,
            "lexique_empreinte": lexique.digest,
            "distance_1_active": lexique.distance_1_active,
            "trivial": lecture.trivial,
            "lecture": lecture.texte,
            "corrections": [
                {"avant": c.avant, "apres": c.apres, "raison": c.raison}
                for c in lecture.corrections
            ],
            "mots_inconnus": lecture.inconnus or None,
            "consignes_du_contexte": [
                {"regle": c.regle, "cote": c.cote, "extrait": c.extrait} for c in consignes
            ]
            or None,
            "arret": verdict.arret,
            "signaux": [
                {"genre": s.genre, "regle": s.regle, "libelle": s.libelle} for s in verdict.signaux
            ]
            or None,
            "question": verdict.question,
            "bloc": rendre(lecture, verdict) or "(aucun bloc : rien a dire)",
            "part_injectee": cfg.optimizer_share,
            "note": (
                "simulation : rien n'a ete injecte ni enregistre"
                if cfg.optimizer_share
                else "simulation ; de plus optimizer_share vaut 0, donc rien n'est injecte en "
                "production non plus"
            ),
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
    f.add_argument("--before", help="anonymiser avant cette date (AAAA-MM-JJ)")
    f.add_argument("--conv", help="anonymiser une conversation")
    f.set_defaults(func=cmd_forget)

    e = sub.add_parser("export", help="sauvegarder les quatre tables irremplacables")
    e.add_argument("-o", "--output", help="fichier de sortie")
    e.set_defaults(func=cmd_export)

    o = sub.add_parser("optimize", help="passer une demande dans l'optimiseur, sans rien injecter")
    o.add_argument("texte", help="la demande a lire")
    o.add_argument(
        "--contexte",
        help="un message anterieur de la meme conversation, pour eprouver la detection des "
        "contradictions avec le contexte",
    )
    o.set_defaults(func=cmd_optimize)

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
