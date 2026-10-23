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


def confronter(
    store: Store, cas: list[dict], fenetre: float = FENETRE_SECONDES
) -> dict:
    """Le rappel@1 de GK et celui de gb, sur exactement les memes demandes."""
    juges = [c for c in cas if attendu_de(c)]
    if not juges:
        return {
            "ok": False,
            "erreur": "aucun cas ne designe de skill : rien a confronter",
            "suite": "annoter l'echantillon, puis gk confronter < fichier",
        }

    paires = _apparier(store, [c.get("demande") or "" for c in juges])

    comparables = 0
    par_prefixe = 0
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

        session = paire["session_id"]
        charges_fenetre = [
            r["skill_name"] for r in store.query(
                "SELECT skill_name FROM skill_uses WHERE session_id = ? "
                "AND ts >= ? AND ts <= ? ORDER BY ts",
                (session, paire["ts"], paire["ts"] + fenetre),
            )
        ]
        charges_session = {
            r["skill_name"] for r in store.query(
                "SELECT DISTINCT skill_name FROM skill_uses WHERE session_id = ?",
                (session,),
            )
        }

        gb_ok_fenetre = attendu in charges_fenetre
        gb_ok_session = attendu in charges_session
        if not charges_session:
            gb_sans_chargement += 1

        gk_juste += int(gk_ok)
        gb_juste_fenetre += int(gb_ok_fenetre)
        gb_juste_session += int(gb_ok_session)
        details.append({
            "numero": c["numero"],
            "attendu": attendu,
            "gk_rang_1": gk_premier,
            "gk_juste": gk_ok,
            "gb_a_charge": sorted(charges_session) or None,
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

    out = {
        "ok": True,
        "cas_juges": len(juges),
        "comparables": comparables,
        "non_apparies": non_apparies,
        "apparies_par_prefixe": par_prefixe,
        # Les deux chiffres, sur exactement le meme sous-ensemble.
        "gk_rappel_1": part(gk_juste),
        "gb_rappel_fenetre": part(gb_juste_fenetre),
        "gb_rappel_session": part(gb_juste_session),
        "gb_sans_aucun_chargement": gb_sans_chargement,
        "ecart_gk_moins_gb": round(part(gk_juste) - part(gb_juste_session), 3),
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
    if r["apparies_par_prefixe"]:
        lignes.append(
            f"{r['apparies_par_prefixe']} appariees par prefixe (demande "
            "multiligne d'un echantillon ancien), donc moins surement."
        )
    lignes.append(
        "L'appariement demande/chargement reste une CORRELATION : les deux "
        "hooks ne partagent aucune clef. Un ecart de quelques points ne "
        f"signifie rien a n = {n} ; un ecart de vingt, si."
    )
    return lignes
