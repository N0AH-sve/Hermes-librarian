"""GK contre gb, sur les memes demandes et la meme verite terrain.

**La comparaison qui n'a jamais ete faite.** Tout ce que le projet a mesure
jusqu'ici compare GK a une etiquette : rappel@1 de 0,255 a 0,400 sur de vraies
demandes, 0,448 au banc. Mais la question qui decide de la phase 3 n'est pas
« GK est-il bon ? », c'est **« GK est-il meilleur que gb ? »** -- et le chiffre
de gb n'existe pas.

Le bras « nom + description » du banc en etait un substitut, et un mauvais : il
fait tourner le moteur de GK sur deux champs, alors que gb est un modele de
langage qui lit 186 descriptions d'un coup. Comparer GK a une version appauvrie
de lui-meme ne dit rien de gb.

Or les deux donnees necessaires sont deja en base, et aucune injection n'est
requise pour les lire :

* `requests` porte la demande (par `pre_llm_call`) ;
* `skill_uses` porte **le skill que gb a reellement charge** (par
  `on_skill_lifecycle`) ;
* l'echantillon annote porte **le bon skill**, juge par un humain.

Trois honnetetes, parce que la conclusion portera une decision.

**L'appariement reste une correlation.** `pre_llm_call` et `on_skill_lifecycle`
ne partagent aucune clef, donc on rapproche par session et par temps. C'est
documente comme une faiblesse depuis le premier jour, et ca ne devient pas une
verite ici.

**Deux fenetres, et la plus genereuse pour gb.** La fenetre courte (apres la
demande) a deja rendu zero : gb charge surtout en debut de session, donc une
demande en milieu de conversation n'a aucun chargement apres elle. La mesure de
session -- « gb a-t-il charge le bon skill **a un moment** de cette
conversation ? » -- credite gb meme d'un chargement anterieur a la demande.
Elle le favorise deliberement : **si gb perd sous une regle qui l'avantage, la
conclusion tient.**

**L'abstention n'est pas une erreur.** Une session ou gb n'a rien charge est
comptee a part. Confondre « gb s'est trompe » et « gb n'a rien charge » rendrait
le chiffre inutilisable, puisque les deux ont des remedes opposes.
"""

from __future__ import annotations

from .store import Store

FENETRE_SECONDES = 120.0
# Les rangs ou les deux courbes sont comparees.
RANGS = (1, 2, 3, 5)

# Seules ces actions sont un USAGE d'un skill par gb.
#
# Releve en production : `skill_uses` porte aussi `patched` (206), `edited`
# (10), `created` (4) et `stale` (8) -- des evenements d'**ecriture** de skill,
# pas de lecture. Les compter comme des choix de gb etait un defaut de la
# premiere confrontation : un skill qu'un collegue venait de corriger entrait
# dans la sequence de gb et pouvait en occuper le rang 1.
ACTIONS_USAGE = ("loaded", "reused", "load", "reuse")
_EN_USAGE = " AND action IN (" + ",".join("?" * len(ACTIONS_USAGE)) + ")"


def _une_ligne(texte: str) -> str:
    return " ".join((texte or "").split())


def attendu_de(cas: dict) -> str | None:
    """Le skill que l'humain a designe, par son nom.

    Une reponse peut etre un rang dans la liste montree ou un nom ecrit a la
    main ; les deux designent un skill, et c'est le nom qui sert a confronter.
    """
    if cas.get("nom"):
        return cas["nom"]
    rang = cas.get("reponse")
    if not rang or rang < 1:
        return None
    candidats = cas.get("candidats") or []
    depart = cas.get("premier_rang", 1)
    i = rang - depart
    if 0 <= i < len(candidats):
        return candidats[i]
    return None


def _apparier(store: Store, demandes: list[str]) -> dict[str, dict]:
    """Retrouve la ligne `requests` de chaque demande de l'echantillon.

    Un echantillon produit avant le repli des demandes multilignes ne porte que
    la **premiere ligne** dans son titre. D'ou le repli par prefixe, et le
    comptage separe de ce qui a ete apparie ainsi : une approximation qui se
    compte n'est pas une approximation cachee.
    """
    rows = store.query(
        "SELECT session_id, ts, text_truncated FROM requests "
        "WHERE text_truncated IS NOT NULL AND text_truncated <> '' "
        "ORDER BY ts DESC LIMIT 2000"
    )
    exact: dict[str, dict] = {}
    for r in rows:
        plat = _une_ligne(r["text_truncated"])
        exact.setdefault(plat, {"session_id": r["session_id"] or "", "ts": r["ts"]})

    out: dict[str, dict] = {}
    for d in demandes:
        plat = _une_ligne(d)
        if not plat:
            continue
        trouve = exact.get(plat)
        if trouve:
            out[d] = dict(trouve, appariement="exact")
            continue
        # Repli : le titre ne porte que le debut d'une demande multiligne.
        for cle, v in exact.items():
            if cle.startswith(plat) and len(plat) >= 12:
                out[d] = dict(v, appariement="prefixe")
                break
    return out


def _diagnostic(
    store: Store, n: int, sessions: set[str], attendus: set[str],
    hors_des_cinq: int = 0,
) -> dict:
    """Ce qui peut rendre la comparaison fausse, mesure plutot qu'affirme.

    Deux controles tires de la base, et une limite qui n'en sort pas.
    """
    actions = {
        (r["action"] or "(vide)"): r["n"] for r in store.query(
            "SELECT action, COUNT(*) n FROM skill_uses GROUP BY action "
            "ORDER BY n DESC LIMIT 10"
        )
    }
    uc = store.one(
        "SELECT COUNT(*) t, MAX(use_count) m, "
        "SUM(CASE WHEN use_count > 1 THEN 1 ELSE 0 END) r FROM skill_uses"
    )
    return {
        # L'effectif reel : des cas qui partagent une session ou un meme skill
        # attendu ne sont pas independants, et gonflent toute significativite.
        "cas": n,
        "sessions_distinctes": len(sessions),
        "skills_attendus_distincts": len(attendus),
        # Les reutilisations sont-elles visibles ? Si `action` ne porte que des
        # chargements et qu'aucun `use_count` ne depasse 1, un skill deja
        # charge est invisible et la courbe par tour sous-estime gb.
        "actions_observees": actions,
        "actions_retenues": list(ACTIONS_USAGE),
        "evenements_ecartes_non_usage": sum(
            n for a, n in actions.items() if a not in ACTIONS_USAGE
        ),
        "evenements": (uc["t"] if uc else 0),
        # `use_count` jamais renseigne compte comme une reutilisation
        # invisible, pas comme une absence d'information : c'est le meme effet
        # sur la mesure.
        "use_count_max": (uc["m"] if uc else None),
        "evenements_use_count_sup_1": int((uc["r"] or 0) if uc else 0),
        # Le biais de selection n'est pas une fatalite : il depend du format
        # d'annotation, et il se leve.
        "etiquettes_hors_des_cinq": hors_des_cinq,
        "biais_de_selection": (
            "LEVE : "
            f"{hors_des_cinq} etiquettes designent un skill que GK ne "
            "proposait pas. La verite terrain ne sort donc plus de la liste de "
            "GK, et la comparaison avec gb est franche."
            if hors_des_cinq else
            "PRESENT : la verite terrain vient des cinq propositions de GK, "
            "donc les cas comparables sont ceux ou GK a REUSSI son rappel@5. "
            "GK est juge sur un sous-ensemble que sa propre reussite a "
            "selectionne, et gb n'a pas cet avantage. Un echantillon v2, ou "
            "l'annotateur peut nommer un skill hors des cinq, leve ce biais."
        ),
    }


def confronter(
    store: Store, cas: list[dict], fenetre: float = FENETRE_SECONDES
) -> dict:
    """Le rappel@1 de GK et celui de gb, sur exactement les memes demandes."""
    # Une etiquette produite par l'une des parties ne peut pas arbitrer entre
    # les deux. Refus, pas avertissement : l'avertissement sur le biais de
    # selection a disparu du raisonnement deux fois dans la meme journee.
    from .jugement import annotateur_de

    nom, circulaire = annotateur_de(cas)
    if circulaire:
        return {
            "ok": False,
            "erreur": (
                f"annotateur « {nom or '(non signe)'} » : la reference a ete "
                "produite par l'agent qu'on evalue, ou son origine n'est pas "
                "etablie. Comparer GK a gb sur ces etiquettes mesure la "
                "coherence de gb avec lui-meme, pas sa justesse"
            ),
            "cas_lus": len(cas),
            "suite": (
                "faire signer l'echantillon par la personne qui l'a annote "
                "(ligne `ANNOTATEUR:`), ou produire des etiquettes humaines"
            ),
            "ce_qui_reste_utilisable": (
                "`gk reetalonner` et `gk ordonnance` mesurent l'ACCORD de GK "
                "avec l'annotateur, ce qui reste informatif sans arbitrer"
            ),
        }
    juges = [c for c in cas if attendu_de(c)]
    if not juges:
        return {
            "ok": False,
            "erreur": "aucun cas ne designe de skill : rien a confronter",
            "suite": "annoter l'echantillon, puis gk confronter < fichier",
        }

    # Garde-fou : le skill designe doit exister au catalogue.
    #
    # La resolution d'un rang en nom passe par la liste des candidats relue
    # dans le fichier. Sur un echantillon produit avant le repli des demandes
    # multilignes, une ligne du texte de l'utilisateur peut ressembler a un
    # candidat (« 2. ... ») et decaler toute la liste. Le chiffre serait alors
    # faux sans rien signaler, juste avant de porter une decision.
    connus = {
        r["name"] for r in store.query(
            "SELECT name FROM skills WHERE missing_since IS NULL"
        )
    }
    inconnus = [
        {"numero": c["numero"], "designe": attendu_de(c)}
        for c in juges if connus and attendu_de(c) not in connus
    ]
    # Refus seulement si c'est un derapage de format, pas un accident isole :
    # au moins trois cas ET plus d'un quart. Sur deux cas juges, un seul
    # inconnu n'autorise aucune conclusion sur le format.
    if len(inconnus) >= 3 and len(inconnus) * 4 > len(juges):
        return {
            "ok": False,
            "erreur": (
                f"{len(inconnus)} cas sur {len(juges)} designent un skill "
                "absent du catalogue : la relecture des candidats a derape, "
                "probablement sur une demande multiligne d'un echantillon v1"
            ),
            "exemples": inconnus[:5],
            "suite": (
                "verifier le fichier a ces numeros, ou regenerer un echantillon "
                "avec `gk echantillon` (v2, demandes repliees sur une ligne)"
            ),
        }
    juges = [c for c in juges if not connus or attendu_de(c) in connus]

    paires = _apparier(store, [c.get("demande") or "" for c in juges])

    comparables = 0
    par_prefixe = 0
    gk_courbe = {k: 0 for k in RANGS}
    gb_courbe = {k: 0 for k in RANGS}
    gb_a_choisi = 0
    gb_deja_avant = 0
    gb_tour_ou_avant = 0
    hors_des_cinq_vus = 0
    sessions_vues: set[str] = set()
    attendus_vus: set[str] = set()
    gk_juste = 0
    gb_juste_fenetre = 0
    gb_juste_session = 0
    gb_sans_chargement = 0
    non_apparies = 0
    details: list[dict] = []

    for c in juges:
        attendu = attendu_de(c)
        demande = c.get("demande") or ""
        paire = paires.get(demande)
        if not paire:
            non_apparies += 1
            continue
        comparables += 1
        if paire["appariement"] == "prefixe":
            par_prefixe += 1

        candidats = c.get("candidats") or []
        depart = c.get("premier_rang", 1)
        # Le rang 1 de GK n'existe que si la liste commence au rang 1 : une
        # reprise commence a 6, et son premier candidat n'est pas un rang 1.
        gk_premier = candidats[0] if (candidats and depart == 1) else None
        gk_ok = bool(gk_premier) and gk_premier == attendu
        # Une etiquette nommee a la main, hors de la liste montree : c'est elle
        # qui leve le biais de selection.
        if c.get("nom"):
            hors_des_cinq_vus += 1

        session = paire["session_id"]
        charges_fenetre = [
            r["skill_name"] for r in store.query(
                "SELECT skill_name FROM skill_uses WHERE session_id = ? "
                "AND ts >= ? AND ts <= ?" + _EN_USAGE + " ORDER BY ts",
                (session, paire["ts"], paire["ts"] + fenetre, *ACTIONS_USAGE),
            )
        ]
        # La SEQUENCE de choix de gb pour cette demande : ses chargements entre
        # cette demande et la suivante de la meme session, dans l'ordre.
        #
        # C'est la seule attribution qui compare des CHOIX. « Le bon skill est
        # parmi les neuf charges dans la session » mesure un ratissage, pas une
        # decision : gb ne choisit pas un skill, il en charge neuf. Pour savoir
        # lequel des deux choisit mieux, il faut le premier de chacun.
        suivante = store.one(
            "SELECT MIN(ts) t FROM requests WHERE session_id = ? AND ts > ?",
            (session, paire["ts"]),
        )
        borne = (suivante["t"] if suivante and suivante["t"] else None)
        if borne:
            sequence = [
                r["skill_name"] for r in store.query(
                    "SELECT skill_name FROM skill_uses WHERE session_id = ? "
                    "AND ts >= ? AND ts < ?" + _EN_USAGE + " ORDER BY ts",
                    (session, paire["ts"], borne, *ACTIONS_USAGE),
                )
            ]
        else:
            sequence = [
                r["skill_name"] for r in store.query(
                    "SELECT skill_name FROM skill_uses WHERE session_id = ? "
                    "AND ts >= ?" + _EN_USAGE + " ORDER BY ts",
                    (session, paire["ts"], *ACTIONS_USAGE),
                )
            ]
        # Un meme skill recharge ne compte qu'une fois : c'est un rang, pas un
        # volume.
        vus: list[str] = []
        for nom in sequence:
            if nom not in vus:
                vus.append(nom)
        sequence = vus
        charges_session = {
            r["skill_name"] for r in store.query(
                "SELECT DISTINCT skill_name FROM skill_uses WHERE session_id = ?"
                + _EN_USAGE,
                (session, *ACTIONS_USAGE),
            )
        }

        gb_ok_fenetre = attendu in charges_fenetre
        gb_ok_session = attendu in charges_session
        if not charges_session:
            gb_sans_chargement += 1

        # gb AVAIT-IL DEJA le bon skill avant cette demande ?
        #
        # Verification la plus importante de la mesure. `on_skill_lifecycle`
        # peut ne pas signaler une reutilisation : un skill charge au tour 1 et
        # encore utile au tour 5 ne produit alors aucun evenement au tour 5. La
        # courbe par tour compterait gb en echec alors qu'il avait la bonne
        # reponse en contexte. Sans ce controle, le 0,133 de gb pourrait etre un
        # artefact de l'instrument et non une mesure de son choix.
        avant = {
            r["skill_name"] for r in store.query(
                "SELECT DISTINCT skill_name FROM skill_uses "
                "WHERE session_id = ? AND ts < ?" + _EN_USAGE,
                (session, paire["ts"], *ACTIONS_USAGE),
            )
        }
        gb_avait_deja = attendu in avant
        if gb_avait_deja:
            gb_deja_avant += 1
        if gb_avait_deja or (attendu in sequence):
            gb_tour_ou_avant += 1

        sessions_vues.add(session)
        attendus_vus.add(attendu)

        gk_juste += int(gk_ok)
        gb_juste_fenetre += int(gb_ok_fenetre)
        gb_juste_session += int(gb_ok_session)

        # Les deux courbes de rang, sur exactement les memes demandes.
        gk_rang = c.get("reponse") if (c.get("reponse") or 0) >= 1 else None
        gb_rang = (sequence.index(attendu) + 1) if attendu in sequence else None
        for k in RANGS:
            if gk_rang is not None and gk_rang <= k:
                gk_courbe[k] += 1
            if gb_rang is not None and gb_rang <= k:
                gb_courbe[k] += 1
        if sequence:
            gb_a_choisi += 1

        details.append({
            "numero": c["numero"],
            "attendu": attendu,
            "gk_rang_1": gk_premier,
            "gk_rang_du_bon": gk_rang,
            "gk_juste": gk_ok,
            "gb_a_choisi_dans_l_ordre": sequence or None,
            "gb_rang_du_bon": gb_rang,
            "gb_a_charge_dans_la_session": len(charges_session),
            "gb_juste": gb_ok_session,
        })

    if not comparables:
        return {
            "ok": False,
            "erreur": (
                "aucune demande de l'echantillon ne se retrouve dans `requests` : "
                "les textes ont peut-etre expire (90 jours) ou ete effaces"
            ),
            "cas_juges": len(juges),
            "non_apparies": non_apparies,
        }

    def part(n: int) -> float:
        return round(n / comparables, 3)

    def path_zero(n: int, d: int) -> float:
        return round(n / d, 3) if d else 0.0

    out = {
        "ok": True,
        "cas_juges": len(juges),
        "designes_hors_catalogue": len(inconnus),
        "comparables": comparables,
        "non_apparies": non_apparies,
        "apparies_par_prefixe": par_prefixe,
        # Les deux chiffres, sur exactement le meme sous-ensemble.
        "gk_rappel_1": part(gk_juste),
        "gb_rappel_fenetre": part(gb_juste_fenetre),
        "gb_rappel_session": part(gb_juste_session),
        "gb_sans_aucun_chargement": gb_sans_chargement,
        "ecart_gk_moins_gb": round(part(gk_juste) - path_zero(gb_juste_session, comparables), 3),
        # LA comparaison de choix : le rang du bon skill chez l'un et chez
        # l'autre, sur les memes demandes. gb charge neuf skills, donc le
        # comparer a un rang unique de GK etait une erreur de denominateur.
        "courbe_gk": {str(k): round(gk_courbe[k] / comparables, 3) for k in RANGS},
        "courbe_gb": {str(k): round(gb_courbe[k] / comparables, 3) for k in RANGS},
        "gb_a_choisi_quelque_chose": gb_a_choisi,
        # Les controles de la mesure elle-meme.
        "gb_avait_deja_le_bon_avant": gb_deja_avant,
        "gb_juste_tour_ou_deja": part(gb_tour_ou_avant),
        "diagnostic": _diagnostic(
            store, comparables, sessions_vues, attendus_vus,
            hors_des_cinq=hors_des_cinq_vus,
        ),
        "details": details,
    }
    return out


def lecture(r: dict) -> list[str]:
    """Ce que le chiffre autorise a conclure, et rien de plus."""
    if not r.get("ok"):
        return []
    n = r["comparables"]
    lignes = [
        "La seule comparaison qui decide de la phase 3 : GK contre gb, sur les "
        "memes demandes et la meme verite terrain humaine.",
        f"GK place le bon skill en premier : {r['gk_rappel_1']}. "
        f"gb l'a charge dans la session : {r['gb_rappel_session']}. "
        f"Sur {n} demandes comparables.",
        "La mesure de session AVANTAGE gb : elle le credite d'un chargement "
        "meme anterieur a la demande. Si gb perd ici, la conclusion tient.",
    ]
    if r["gb_sans_aucun_chargement"]:
        lignes.append(
            f"{r['gb_sans_aucun_chargement']} sessions sans aucun chargement : "
            "comptees a part, parce qu'une abstention n'est pas une erreur et "
            "n'a pas le meme remede."
        )
    if r["non_apparies"]:
        lignes.append(
            f"{r['non_apparies']} demandes de l'echantillon introuvables en "
            "base : exclues des deux cotes, donc sans biais."
        )
    if r.get("designes_hors_catalogue"):
        lignes.append(
            f"{r['designes_hors_catalogue']} cas ecartes : le skill designe "
            "n'existe pas au catalogue, donc la relecture des candidats a "
            "derape sur ces lignes."
        )
    if r["apparies_par_prefixe"]:
        lignes.append(
            f"{r['apparies_par_prefixe']} appariees par prefixe (demande "
            "multiligne d'un echantillon ancien), donc moins surement."
        )
    cg, cb = r.get("courbe_gk") or {}, r.get("courbe_gb") or {}
    if cg and cb:
        lignes.insert(2, (
            "LA comparaison de choix, au meme rang et sur les memes demandes : "
            f"rang 1, GK {cg.get('1')} contre gb {cb.get('1')} ; "
            f"rang 2, GK {cg.get('2')} contre gb {cb.get('2')} ; "
            f"rang 3, GK {cg.get('3')} contre gb {cb.get('3')}."
        ))
        lignes.insert(3, (
            "La courbe de gb est l'ordre de ses chargements entre cette demande "
            "et la suivante. C'est son premier choix, pas le lot de neuf qu'il "
            "charge dans la session : comparer un rang unique de GK a ce lot "
            "etait une erreur de denominateur."
        ))
    if r.get("gb_a_choisi_quelque_chose") is not None:
        lignes.append(
            f"gb n'a rien charge apres {n - r['gb_a_choisi_quelque_chose']} de "
            f"ces {n} demandes : sa courbe y vaut zero par definition, pas par "
            "erreur."
        )
    d = r.get("diagnostic") or {}
    if r.get("gb_avait_deja_le_bon_avant") is not None:
        lignes.append(
            f"CONTROLE : gb avait deja le bon skill charge AVANT la demande "
            f"dans {r['gb_avait_deja_le_bon_avant']} cas. En creditant gb du "
            f"tour ou d'un chargement anterieur, il passe a "
            f"{r['gb_juste_tour_ou_deja']}. C'est la mesure la plus juste par "
            "demande, et c'est a elle qu'il faut comparer GK."
        )
    if d.get("evenements_ecartes_non_usage"):
        lignes.append(
            f"CONTROLE : {d['evenements_ecartes_non_usage']} evenements ecartes "
            "parce qu'ils ne sont pas un usage (`patched`, `edited`, `created`, "
            "`stale` : un collegue qui corrige un skill, pas gb qui le lit). "
            "Les compter crediterait gb d'un choix qu'il n'a pas fait."
        )
    if d.get("evenements"):
        visibles = (d.get("evenements_use_count_sup_1") or 0) > 0
        lignes.append(
            f"CONTROLE : actions observees {d.get('actions_observees')}, "
            f"use_count max {d.get('use_count_max')}. Les reutilisations sont "
            + ("visibles, donc la courbe par tour de gb est une mesure."
               if visibles else
               "INVISIBLES : un skill deja charge ne produit aucun evenement, "
               "donc la courbe par tour SOUS-ESTIME gb et ne peut pas servir "
               "de comparaison. S'en tenir a `gb_juste_tour_ou_deja`.")
        )
    if d.get("sessions_distinctes") is not None:
        lignes.append(
            f"CONTROLE : {d['cas']} cas, mais seulement "
            f"{d['sessions_distinctes']} sessions distinctes et "
            f"{d['skills_attendus_distincts']} skills attendus distincts. "
            "L'effectif reel est le plus petit des trois, et toute "
            "significativite calculee sur 30 est optimiste."
        )
    if d.get("biais_de_selection"):
        lignes.append("BIAIS DE SELECTION " + d["biais_de_selection"])
    lignes.append(
        "L'appariement demande/chargement reste une CORRELATION : les deux "
        "hooks ne partagent aucune clef. Un ecart de quelques points ne "
        f"signifie rien a n = {n} ; un ecart de vingt, si."
    )
    return lignes
