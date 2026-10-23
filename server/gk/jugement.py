"""L'echantillon a juger, et sa relecture.

Tout est bloque sur le meme manque : **aucune verite terrain sur des demandes
reelles**. Ni le seuil de confiance, ni le vrai rappel, ni la decision
d'injecter un brief. Les corrections de la phase 1 en donneraient, mais il y en
a six.

Le moyen court est de juger a la main : cinquante demandes reelles, cinq skills
proposes pour chacune, et un humain qui dit lequel est le bon. Une heure de
travail donne le rappel@1 et @5 reels, le seuil refixe sur les vraies donnees,
et donc le feu vert ou le refus pour la phase 3.

Le format est volontairement pauvre -- du texte qu'on edite dans n'importe quel
editeur -- parce qu'un format riche coute une interface, et qu'une interface
coute plus que l'heure qu'elle economise.

**Le defaut de la version 1, et pourquoi il comptait.** La v1 n'offrait que deux
reponses : un rang parmi les cinq, ou `0`. L'annotateur n'avait donc aucun moyen
de dire « le bon skill existe, mais il n'est pas dans cette liste ». Trois
consequences, toutes mesurees sur le premier releve reel :

* `rappel_5` valait **mecaniquement** `1 - part_sans_reponse_possible`. Toute
  reponse autre que `0` designe un des cinq par construction, donc le chiffre
  ne portait aucune information sur la qualite de la recherche. 0,638 + 0,362 =
  1,000 exactement, et ce n'etait pas une coincidence ;
* `0` portait deux sens incompatibles -- « aucun skill de la bibliotheque ne
  sert cette demande » et « aucun de ces cinq-la » -- que la lecture imprimee
  confondait en annoncant un trou de la bibliotheque ;
* `rappel_1` restait valide, mais son denominateur devenait une question
  ouverte, donc un intervalle et non un point.

La v2 ajoute une troisieme reponse : **le nom du bon skill**, quand il existe
hors des cinq. Elle ne coute rien a l'annotateur -- un nom au lieu d'un chiffre
-- et elle resout les trois points d'un coup. `reprise` permet de n'annoter que
les cas `0` d'un echantillon v1 deja rempli, pour ne pas refaire l'heure.
"""

from __future__ import annotations

import re

from .recherche import Index, contexte_de_session
from .store import Store

MARQUEUR = "REPONSE:"
VERSION = 2
_MARQUE = "<!-- gk echantillon v"
# Un echantillon stratifie par longueur : les demandes courtes et longues ne
# posent pas le meme probleme, et un tirage simple sur-representerait les
# moyennes.
TRANCHES = ((0, 5), (6, 12), (13, 25), (26, 10_000))
# Profondeur de la seconde passe : les rangs 6 a 15 d'une demande dont
# l'annotateur a dit qu'aucun des cinq premiers ne convenait.
REPRISE_JUSQU_A = 15

_CONSIGNE = (
    "Pour chaque demande, ecrivez apres `REPONSE:` une seule chose :",
    "",
    "  * le NUMERO du bon skill, s'il est dans la liste ;",
    "  * le NOM du bon skill, s'il existe mais n'est PAS dans la liste",
    "    (`gk catalogue --noms` les liste tous) ;",
    "  * `0` si AUCUN skill de la bibliotheque ne sert cette demande ;",
    "  * `?` si vous ne savez pas.",
    "",
    "La distinction entre un nom et `0` est celle qui porte la mesure : sans",
    "elle, le rappel dans les cinq vaut mecaniquement l'inverse du taux de `0`",
    "et ne mesure plus rien.",
)


# Les demandes qui ne viennent pas d'un collegue.
#
# Releve par gb le 2026-10-23 : 53 des 196 demandes d'une journee sont des
# injections du systeme -- bannieres de cycle de vie, avis de tache de fond,
# annonces de piece jointe. `pre_llm_call` porte le message tel que le modele
# le recoit, donc ces blocs y sont. Les juger comme des demandes fausse deux
# choses a la fois : le rappel (aucun skill ne sert une banniere) et surtout la
# part « aucun ne convient », qui en herite directement.
#
# La liste est volontairement etroite et verifiable : `gk demandes --injections`
# montre ce qu'elle attrape, pour qu'un motif trop large se voie au lieu de
# manger des demandes reelles en silence.
# Les plateformes qui ne portent pas un collegue.
#
# **Meilleur filtre que n'importe quel motif de texte, et pour une raison de
# fond : c'est une donnee structuree et non une devinette.** `gk demandes`
# compte 77 demandes `cron` sur 494, soit 16 % : ce sont des taches planifiees,
# pas des questions. Juger GK sur un prompt de cron n'a aucun sens, et les
# compter dans « aucun skill ne convient » gonfle ce taux d'autant.
#
# La premiere version de ce filtre devinait des motifs de texte depuis un
# rapport. Elle attrapait **0 demande sur 494**. La lecon est gardee : un filtre
# se tire des donnees, et `gk demandes --motifs` sert a les regarder.
PLATEFORMES_NON_HUMAINES = ("cron", "scheduler", "timer", "system")

MOTIFS_INJECTION = (
    "skill invoked",
    "background process completed",
    "background task completed",
    "<system-reminder",
    "system-reminder>",
    "[attachment",
    "[image",
    "[file attached",
    "piece jointe recue",
)


def est_injection(texte: str, plateforme: str = "") -> bool:
    """Vrai si la demande ne vient pas d'un collegue.

    Deux criteres, et le premier est le solide : la **plateforme**, donnee
    structuree, ecarte les taches planifiees. Les motifs de texte ne sont qu'un
    filet de secours, et ils n'ont encore rien attrape en production -- ils sont
    gardes sans etre crus.
    """
    if (plateforme or "").strip().lower() in PLATEFORMES_NON_HUMAINES:
        return True
    bas = (texte or "").lower()
    if not bas.strip():
        return True
    return any(m in bas for m in MOTIFS_INJECTION)


def motifs_repetes(
    textes: list[str], prefixe: int = 60, minimum: int = 2
) -> list[dict]:
    """Les debuts de demande qui se repetent, les plus frequents d'abord.

    **Comment trouver un filtre au lieu de le deviner.** Une injection du
    systeme est un gabarit : elle se repete mot pour mot. Une question de
    collegue, non. Grouper les demandes par leur debut fait donc remonter les
    gabarits en tete, et une liste de motifs ecrite ensuite est verifiable
    plutot que supposee.
    """
    groupes: dict[str, int] = {}
    for t in textes:
        cle = " ".join((t or "").split())[:prefixe].lower()
        if not cle:
            continue
        groupes[cle] = groupes.get(cle, 0) + 1
    return [
        {"debut": k, "occurrences": v}
        for k, v in sorted(groupes.items(), key=lambda kv: -kv[1])
        if v >= minimum
    ]


def _une_ligne(texte: str) -> str:
    """Une demande sur une seule ligne.

    Un message d'utilisateur peut contenir des retours a la ligne, et le format
    porte la demande dans un titre `## N. ...`. Sans ce repli, une demande
    multiligne casse la relecture -- et ses lignes suivantes peuvent meme etre
    prises pour des candidats.
    """
    return " ".join((texte or "").split())


def preparer(store: Store, index: Index, taille: int = 50, par_page: int = 5) -> str:
    """Rend l'echantillon, en texte a annoter."""
    from .recherche import jetons

    rows = store.query(
        "SELECT session_id, text_truncated, ts, platform FROM requests "
        "WHERE text_truncated IS NOT NULL AND text_truncated <> '' "
        "ORDER BY ts DESC LIMIT 1000"
    )
    ecartees = sum(
        1 for r in rows if est_injection(r["text_truncated"], r["platform"])
    )
    rows = [
        r for r in rows
        if not est_injection(r["text_truncated"], r["platform"])
    ]
    par_tranche: dict[tuple[int, int], list] = {t: [] for t in TRANCHES}
    for row in rows:
        n = len(jetons(row["text_truncated"]))
        for borne in TRANCHES:
            if borne[0] <= n <= borne[1]:
                par_tranche[borne].append(row)
                break
    choisis: list = []
    quota = max(1, taille // len(TRANCHES))
    for borne in TRANCHES:
        choisis.extend(par_tranche[borne][:quota])
    # On complete avec les plus recentes si une tranche etait maigre.
    if len(choisis) < taille:
        deja = {id(r) for r in choisis}
        choisis.extend(r for r in rows if id(r) not in deja)
    choisis = choisis[:taille]

    lignes = [
        "# Echantillon a juger",
        f"{_MARQUE}{VERSION} -->",
        "",
        *_CONSIGNE,
        "",
        "N'ajoutez rien d'autre : le reste du fichier est relu tel quel.",
        "",
        f"{len(choisis)} demandes, prises parmi les plus recentes et reparties",
        "par longueur, parce qu'une demande courte et une longue ne posent pas le",
        "meme probleme.",
        "",
        f"<!-- {ecartees} demandes ecartees : taches planifiees (plateforme "
        "`cron`) et bannieres du systeme. Aucun skill ne sert un prompt de "
        "cron, et les juger gonflerait la part « aucun ne convient » -->",
        "",
        "---",
        "",
    ]
    for i, row in enumerate(choisis, start=1):
        demande = _une_ligne(row["text_truncated"])
        # `avant` : sans lui, le contexte serait pris a la FIN de la session,
        # donc dans le futur de cette demande.
        contexte = contexte_de_session(
            store, row["session_id"] or "", avant=row["ts"]
        )
        if contexte:
            resultats = index.chercher_avec_contexte(
                demande, contexte=contexte, limite=par_page
            )
        else:
            resultats = index.chercher(demande, limite=par_page)
        lignes.append(f"## {i}. {demande}")
        if contexte:
            lignes.append(f"<!-- contexte : {len(contexte)} tour(s) precedent(s) -->")
        if not resultats:
            lignes.append("   (aucun skill propose)")
        for rang, r in enumerate(resultats, start=1):
            lignes.append(f"   {rang}. {r['name']}   [{r['category']}]  score {r['score']}")
        lignes.append(f"{MARQUEUR} ")
        lignes.append("")
    return "\n".join(lignes) + "\n"


def reprise(
    store: Store,
    index: Index,
    cas: list[dict],
    jusqu_a: int = REPRISE_JUSQU_A,
) -> str:
    """Seconde passe : les seuls cas que l'annotateur a classes `0`.

    Un echantillon v1 ne distingue pas « aucun skill ne sert cette demande » de
    « aucun de ces cinq-la ». La difference se recupere sans refaire l'heure :
    seuls les cas `0` la portent, et pour chacun il suffit de montrer **la
    suite** du classement.

    Les rangs affiches sont les rangs **reels**, donc une reponse `7` se lit
    comme un echec du rang 1 mais pas du rappel de la bibliotheque -- ce que la
    v1 ne pouvait pas exprimer.
    """
    a_revoir = [c for c in cas if c.get("reponse") == 0]
    lignes = [
        "# Reprise : les demandes classees « aucun ne convient »",
        f"{_MARQUE}{VERSION} -->",
        "",
        "Ces demandes seules restent ambigues : vous avez dit qu'aucun des cinq",
        "premiers ne convenait, et le format ne demandait pas si un autre skill",
        "aurait convenu. Voici la SUITE du classement pour chacune.",
        "",
        *_CONSIGNE,
        "",
        "Gardez `0` si vraiment aucun skill de la bibliotheque ne sert la",
        "demande : c'est une reponse, et c'est meme la plus interessante.",
        "",
        "Ce fichier se relit AVEC le premier, et il le corrige :",
        "    cat echantillon.txt reprise.txt | gk juger",
        "",
        "---",
        "",
    ]
    if not a_revoir:
        lignes.append("(aucun cas classe `0` : rien a reprendre)")
        return "\n".join(lignes) + "\n"

    # On retrouve la session pour reproduire le classement d'origine : sans le
    # contexte, la suite affichee ne serait pas la suite du meme classement.
    rows = store.query(
        "SELECT session_id, text_truncated, ts FROM requests "
        "WHERE text_truncated IS NOT NULL AND text_truncated <> '' "
        "ORDER BY ts DESC LIMIT 1000"
    )
    sessions = {
        _une_ligne(r["text_truncated"]): ((r["session_id"] or ""), r["ts"])
        for r in rows
    }

    for c in a_revoir:
        demande = _une_ligne(c.get("demande") or "")
        session, ts_demande = sessions.get(demande, ("", None))
        contexte = (
            contexte_de_session(store, session, avant=ts_demande)
            if session else []
        )
        if contexte:
            resultats = index.chercher_avec_contexte(
                demande, contexte=contexte, limite=jusqu_a
            )
        else:
            resultats = index.chercher(demande, limite=jusqu_a)
        deja = len(c.get("candidats") or [])
        suite = resultats[deja:]
        lignes.append(f"## {c['numero']}. {demande}")
        lignes.append(f"<!-- v1 : les rangs 1 a {deja} ont deja ete ecartes -->")
        if not suite:
            lignes.append(
                f"   (le classement s'arrete au rang {len(resultats)} : "
                "aucun autre candidat)"
            )
        for rang, r in enumerate(suite, start=deja + 1):
            lignes.append(f"   {rang}. {r['name']}   [{r['category']}]  score {r['score']}")
        lignes.append(f"{MARQUEUR} ")
        lignes.append("")
    return "\n".join(lignes) + "\n"


def etiquettes(cas: list[dict]) -> list[dict]:
    """Les paires (demande, bon skill) reutilisables d'un echantillon annote.

    **Ce qui survit a une regeneration de l'echantillon, et ce qui n'y survit
    pas.** Une reponse qui designe un skill est un jugement sur la **demande** :
    « pour cette demande, le bon skill est X. » Elle reste vraie si le
    classement change, donc elle est reutilisable indefiniment.

    Une reponse `0` ne dit pas « aucun skill ne sert cette demande » : elle dit
    « aucun des cinq **que GK proposait alors** ». Avec un autre classement, les
    cinq ne sont plus les memes, donc l'information est perdue. Ces cas sont
    ecartes et comptes, jamais convertis en « aucune reponse ».

    C'est ce qui permet de recuperer une heure d'annotation apres la correction
    de la fuite de contexte, au lieu de la refaire.
    """
    from .confrontation import attendu_de

    out: list[dict] = []
    for c in cas:
        attendu = attendu_de(c)
        demande = _une_ligne(c.get("demande") or "")
        if not attendu or not demande or est_injection(demande):
            continue
        out.append({"numero": c["numero"], "demande": demande, "attendu": attendu})
    return out


def reetalonner(
    store: Store, index: Index, cas: list[dict], rangs=(1, 2, 3, 5)
) -> dict:
    """Le rappel reel, re-mesure sur les etiquettes avec le contexte corrige.

    Aucune nouvelle annotation : les etiquettes de l'echantillon sont rejouees
    contre un classement refait, ancre sur la demande et non sur la fin de la
    session.
    """
    etqs = etiquettes(cas)
    annotes = [c for c in cas if c.get("reponse") is not None or c.get("nom")]
    zeros = sum(1 for c in annotes if c.get("reponse") == 0)
    injections = sum(
        1 for c in cas if est_injection(_une_ligne(c.get("demande") or ""))
    )
    if not etqs:
        return {
            "ok": False,
            "erreur": "aucune etiquette reutilisable dans cet echantillon",
            "cas_lus": len(cas),
            "reponses_0_non_reutilisables": zeros,
        }

    # On retrouve session et horodatage pour rejouer le contexte a l'identique.
    situe, plateformes = _situer(store)

    # Une etiquette portant sur une tache planifiee n'est pas une etiquette :
    # la plateforme n'est connue qu'ici, pas dans le fichier annote.
    avant_plateforme = len(etqs)
    etqs = [
        e for e in etqs
        if not est_injection(e["demande"], plateformes.get(e["demande"], ""))
    ]
    ecartees_plateforme = avant_plateforme - len(etqs)
    if not etqs:
        return {
            "ok": False,
            "erreur": (
                "toutes les etiquettes portent sur des demandes non humaines "
                "(taches planifiees)"
            ),
            "cas_lus": len(cas),
            "etiquettes_ecartees_plateforme": ecartees_plateforme,
        }

    courbe = {k: 0 for k in rangs}
    distribution: dict[str, int] = {}
    hors = 0
    avec_contexte = 0
    details: list[dict] = []
    for e in etqs:
        session, ts = situe.get(e["demande"], ("", None))
        contexte = (
            contexte_de_session(store, session, avant=ts)
            if session and ts is not None else []
        )
        if contexte:
            avec_contexte += 1
            resultats = index.chercher_avec_contexte(
                e["demande"], contexte=contexte, limite=max(rangs)
            )
        else:
            resultats = index.chercher(e["demande"], limite=max(rangs))
        noms = [r["name"] for r in resultats]
        rang = noms.index(e["attendu"]) + 1 if e["attendu"] in noms else None
        if rang is None:
            hors += 1
        else:
            distribution[str(rang)] = distribution.get(str(rang), 0) + 1
            for k in rangs:
                if rang <= k:
                    courbe[k] += 1
        details.append({
            "numero": e["numero"], "attendu": e["attendu"], "rang": rang,
            "contexte_tours": len(contexte),
        })

    n = len(etqs)
    return {
        "ok": True,
        "cas_lus": len(cas),
        "etiquettes_reutilisables": n,
        "reponses_0_non_reutilisables": zeros,
        "injections_ecartees": injections,
        "etiquettes_ecartees_plateforme": ecartees_plateforme,
        "demandes_avec_contexte": avec_contexte,
        "courbe": {str(k): round(courbe[k] / n, 3) for k in rangs},
        "distribution_des_rangs": distribution,
        f"hors_des_{max(rangs)}": hors,
        "details": details,
    }


BONUS_CANDIDATS = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0)


def _situer(store: Store) -> tuple[dict, dict]:
    """Session, horodatage et plateforme de chaque demande enregistree."""
    rows = store.query(
        "SELECT session_id, text_truncated, ts, platform FROM requests "
        "WHERE text_truncated IS NOT NULL AND text_truncated <> '' "
        "ORDER BY ts DESC LIMIT 2000"
    )
    situe: dict[str, tuple[str, float]] = {}
    plats: dict[str, str] = {}
    for r in rows:
        plat = _une_ligne(r["text_truncated"])
        situe.setdefault(plat, (r["session_id"] or "", r["ts"]))
        plats.setdefault(plat, r["platform"] or "")
    return situe, plats


def _rejouer(
    store, index, etqs: list[dict], situe: dict, bonus: float,
    top: int, rangs: tuple[int, ...],
) -> dict:
    """Rejoue chaque session dans l'ordre, en gardant la memoire de GK.

    L'ordre compte : une memoire se construit, donc une demande ne peut etre
    jugee qu'apres celles qui la precedent dans sa conversation.
    """
    par_session: dict[str, list[dict]] = {}
    for e in etqs:
        session, ts = situe.get(e["demande"], ("", None))
        if ts is None:
            continue
        par_session.setdefault(session, []).append(dict(e, ts=ts, session=session))
    for v in par_session.values():
        v.sort(key=lambda e: e["ts"])

    courbe = {k: 0 for k in rangs}
    n = 0
    rattrapes = 0
    for session, demandes in par_session.items():
        memoire: set[str] = set()
        for e in demandes:
            contexte = contexte_de_session(store, session, avant=e["ts"])
            sans = index.chercher(e["demande"], limite=max(rangs))
            if bonus > 0:
                avec = index.chercher_avec_memoire(
                    e["demande"], memoire=memoire, bonus=bonus,
                    limite=max(rangs), contexte=contexte or None,
                )
            elif contexte:
                avec = index.chercher_avec_contexte(
                    e["demande"], contexte=contexte, limite=max(rangs)
                )
            else:
                avec = sans
            noms = [r["name"] for r in avec]
            rang = noms.index(e["attendu"]) + 1 if e["attendu"] in noms else None
            noms_sans = [r["name"] for r in sans[: max(rangs)]]
            if rang is not None and e["attendu"] not in noms_sans:
                rattrapes += 1
            n += 1
            if rang is not None:
                for k in rangs:
                    if rang <= k:
                        courbe[k] += 1
            # La memoire se remplit APRES le jugement : sinon le bon skill du
            # tour courant s'y trouverait deja, et la mesure serait circulaire.
            memoire |= {r["name"] for r in avec[:top]}
    return {
        "n": n,
        "courbe": {str(k): round(courbe[k] / n, 3) if n else None for k in rangs},
        "rattrapes_par_la_memoire": rattrapes,
        "sessions": len(par_session),
    }


def simuler_memoire(
    store: Store,
    index: Index,
    cas: list[dict],
    top: int = 3,
    rangs: tuple[int, ...] = (1, 2, 3, 5),
) -> dict:
    """GK avec la memoire de session que gb a deja, mesure honnetement.

    **Pourquoi ce signal, et pourquoi maintenant.** gb a le bon skill en
    contexte 81 % du temps parce qu'il charge large au premier tour et **garde**.
    GK recalculait tout a chaque demande. Le mecanisme de gb, implemente dans
    GK, est donc le candidat le plus serieux -- et il n'etait pas mesurable
    avant : l'etiquette disponible etait « le skill que gb a charge », donc
    mesurer une memoire de skills charges aurait ete circulaire. Les etiquettes
    humaines de la v2 levent cela.

    **Le decoupage se fait par SESSION, pas par demande.** Les demandes d'une
    meme conversation partagent presque toujours le meme bon skill -- 21 skills
    pour 12 sessions au releve -- donc deux demandes d'une session ne sont pas
    deux observations. Ajuster sur une moitie des demandes et verifier sur
    l'autre laisserait la memoire d'une session servir dans les deux.

    Le bonus est un parametre, donc il s'ajuste sur une moitie et se verifie sur
    l'autre. La derniere fois qu'un seuil a ete ajuste sans cette precaution, il
    passait de 1,0 a 0,333 entre les deux moities.
    """
    etqs = etiquettes(cas)
    situe, plats = _situer(store)
    etqs = [e for e in etqs if not est_injection(e["demande"], plats.get(e["demande"], ""))]
    if not etqs:
        return {"ok": False, "erreur": "aucune etiquette reutilisable"}

    sessions = sorted({situe.get(e["demande"], ("", 0))[0] for e in etqs})
    if len(sessions) < 4:
        return {
            "ok": False,
            "erreur": (
                f"{len(sessions)} sessions seulement : impossible de couper en "
                "deux moities qui veuillent dire quelque chose"
            ),
            "etiquettes": len(etqs),
        }
    moitie = len(sessions) // 2
    ajust, verif = set(sessions[:moitie]), set(sessions[moitie:])
    lot = {
        "ajustement": [e for e in etqs if situe.get(e["demande"], ("", 0))[0] in ajust],
        "verification": [e for e in etqs if situe.get(e["demande"], ("", 0))[0] in verif],
    }

    # Ajustement : le bonus qui maximise le rang 1 sur la premiere moitie.
    essais = []
    for b in BONUS_CANDIDATS:
        r = _rejouer(store, index, lot["ajustement"], situe, b, top, rangs)
        essais.append({"bonus": b, **r})
    retenu = max(
        essais, key=lambda r: (r["courbe"].get("1") or 0, r["courbe"].get("5") or 0)
    )

    sans_memoire = _rejouer(store, index, lot["verification"], situe, 0.0, top, rangs)
    avec_memoire = _rejouer(
        store, index, lot["verification"], situe, retenu["bonus"], top, rangs
    )
    return {
        "ok": True,
        "etiquettes": len(etqs),
        "sessions": len(sessions),
        "top_garde_par_tour": top,
        "bonus_retenu": retenu["bonus"],
        "essais_sur_l_ajustement": [
            {"bonus": e["bonus"], "rang_1": e["courbe"].get("1"),
             "rang_5": e["courbe"].get("5")} for e in essais
        ],
        "verification_sans_memoire": sans_memoire["courbe"],
        "verification_avec_memoire": avec_memoire["courbe"],
        "cas_de_verification": sans_memoire["n"],
        "sessions_de_verification": sans_memoire["sessions"],
        "rattrapes_par_la_memoire": avec_memoire["rattrapes_par_la_memoire"],
    }


_TITRE = re.compile(r"^##\s*(\d+)\.\s*(.*)$")
_CANDIDAT = re.compile(r"^\s*(\d+)\.\s+(\S+)\s")
_VERSION = re.compile(re.escape(_MARQUE) + r"(\d+)")
_NOM = re.compile(r"^[A-Za-z][A-Za-z0-9._/-]{2,}$")


def relire(texte: str) -> list[dict]:
    """Relit l'echantillon annote. Tolerant : une ligne mal remplie est signalee,
    pas fatale.

    Deux fichiers concatenes se relisent ensemble, et **le dernier gagne** :
    c'est ce qui permet a `gk reprise` de corriger les cas ambigus du premier
    sans refaire l'annotation entiere.
    """
    version = 1
    marque = _VERSION.search(texte or "")
    if marque:
        version = int(marque.group(1))

    cas: list[dict] = []
    courant: dict | None = None
    for ligne in (texte or "").splitlines():
        titre = _TITRE.match(ligne)
        if titre:
            if courant is not None:
                cas.append(courant)
            courant = {
                "numero": int(titre.group(1)), "demande": titre.group(2),
                "candidats": [], "reponse": None, "nom": None, "brut": "",
                "version": version,
            }
            continue
        if courant is None:
            continue
        candidat = _CANDIDAT.match(ligne)
        if candidat and not ligne.lstrip().startswith(MARQUEUR):
            courant["candidats"].append(candidat.group(2))
            # Le rang affiche est le rang reel : une reprise commence a 6.
            courant.setdefault("premier_rang", int(candidat.group(1)))
            continue
        if ligne.strip().startswith(MARQUEUR):
            brut = ligne.split(MARQUEUR, 1)[1].strip()
            courant["brut"] = brut
            if brut.isdigit():
                courant["reponse"] = int(brut)
            elif _NOM.match(brut):
                # Le bon skill existe, hors de la liste montree. Si c'est un des
                # candidats affiches, on le ramene a son rang.
                courant["nom"] = brut
                rangs = courant.get("candidats") or []
                depart = courant.get("premier_rang", 1)
                for i, nom in enumerate(rangs):
                    if nom == brut:
                        courant["reponse"] = depart + i
                        courant["nom"] = None
                        break
    if courant is not None:
        cas.append(courant)

    # Le dernier cas portant une reponse l'emporte, par numero.
    fusion: dict[int, dict] = {}
    for c in cas:
        n = c["numero"]
        if n not in fusion:
            fusion[n] = c
        elif c["reponse"] is not None or c["nom"]:
            fusion[n] = c
    return [fusion[n] for n in sorted(fusion)]


def juger(cas: list[dict], dans_les_n: int = 5) -> dict:
    """Le rappel reel, sur le seul denominateur qui ait un sens.

    **Le denominateur est le nombre de demandes qu'un skill de la bibliotheque
    peut servir.** Une demande qu'aucun skill ne sert n'est pas un echec de
    recherche, et la compter en abaisserait le chiffre sans rien apprendre.

    Quand l'echantillon est en v1, ce denominateur n'est pas connu : les `0`
    melangent les trous de la bibliotheque et les skills simplement manques.
    Le resultat est alors rendu **en intervalle**, et le point n'est pas
    annonce -- un chiffre faux est plus couteux qu'un chiffre absent.
    """
    annotes = [c for c in cas if c.get("reponse") is not None or c.get("nom")]
    total = len(annotes)
    if not total:
        return {
            "ok": False,
            "erreur": "aucune reponse annotee",
            "cas_lus": len(cas),
            "non_annotes": len(cas),
        }

    version = max((c.get("version") or 1) for c in annotes)
    rangs = [c["reponse"] for c in annotes if c.get("reponse")]
    premier = sum(1 for r in rangs if r == 1)
    dans_cinq = sum(1 for r in rangs if r <= dans_les_n)
    hors_cinq = sum(1 for r in rangs if r > dans_les_n)
    hors_cinq += sum(1 for c in annotes if c.get("nom"))
    aucun = sum(1 for c in annotes if c.get("reponse") == 0)

    # Le rang auquel le bon skill se trouve QUAND il est cite : c'est lui qui
    # separe un probleme de classement d'un probleme de recherche. Le bon skill
    # au rang 2 se corrige par des coefficients ; au rang 5, a peine.
    distribution = {}
    for r in sorted(set(rangs)):
        distribution[str(r)] = sum(1 for x in rangs if x == r)

    out = {
        "ok": True,
        "cas_lus": len(cas),
        "cas_annotes": total,
        "non_annotes": len(cas) - total,
        "version_echantillon": version,
        f"designe_dans_les_{dans_les_n}": dans_cinq,
        "designe_au_rang_1": premier,
        f"designe_hors_des_{dans_les_n}": hors_cinq,
        "aucun_skill_ne_convient": aucun,
        "distribution_des_rangs": distribution,
        # Le taux de CITATION sur toutes les demandes annotees. Celui-la ne
        # depend d'aucune hypothese, et c'est le denominateur que voit un brief :
        # un brief ne sait pas si une demande a une reponse.
        f"cite_dans_les_{dans_les_n}_sur_tout": round(dans_cinq / total, 3),
        "part_premier_quand_cite": (
            round(premier / dans_cinq, 3) if dans_cinq else None
        ),
    }

    servables = dans_cinq + hors_cinq
    if version >= 2:
        # `0` veut dire ce qu'il dit : le denominateur est connu.
        out["demandes_servables"] = servables
        out["part_sans_reponse_possible"] = round(aucun / total, 3)
        if servables:
            out["rappel_1_reel"] = round(premier / servables, 3)
            out[f"rappel_{dans_les_n}_reel"] = round(dans_cinq / servables, 3)
        else:
            out["rappel_1_reel"] = None
            out[f"rappel_{dans_les_n}_reel"] = None
            out["avertissement"] = (
                "aucune demande annotee n'a de reponse dans la bibliotheque : "
                "rien a mesurer"
            )
        return out

    # v1 : les `0` sont ambigus, donc le denominateur est un intervalle.
    out["ambigus"] = aucun
    out["rappel_1_borne_basse"] = round(premier / total, 3)
    out["rappel_1_borne_haute"] = (
        round(premier / servables, 3) if servables else None
    )
    out[f"rappel_{dans_les_n}_borne_basse"] = round(dans_cinq / total, 3)
    out[f"rappel_{dans_les_n}_borne_haute"] = (
        round(dans_cinq / servables, 3) if servables else None
    )
    out["avertissement"] = (
        f"echantillon v1 : les {aucun} reponses `0` ne distinguent pas « aucun "
        "skill ne sert cette demande » de « aucun de ces cinq-la ». Le rappel "
        f"dans les {dans_les_n} y vaut mecaniquement l'inverse du taux de `0` "
        "et ne mesure rien. `gk reprise` leve l'ambiguite sans refaire "
        "l'annotation entiere."
    )
    return out
