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
import sys
from pathlib import Path

from .catalog import Catalog
from .config import Config
from .corrections import (
    BAD_RECOMMENDATION,
    BRIEF_IGNORED,
    UNATTRIBUTABLE,
    retire_testcases,
    tidy,
)
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
    f.add_argument("--before", help="anonymiser avant cette date (AAAA-MM-JJ)")
    f.add_argument("--conv", help="anonymiser une conversation")
    f.set_defaults(func=cmd_forget)

    e = sub.add_parser("export", help="sauvegarder les quatre tables irremplacables")
    e.add_argument("-o", "--output", help="fichier de sortie")
    e.set_defaults(func=cmd_export)

    sub.add_parser(
        "catalogue", help="l'etat du catalogue recu par manifeste"
    ).set_defaults(func=cmd_catalogue)

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
