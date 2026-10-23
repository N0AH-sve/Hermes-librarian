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
MARQUEUR_ANNOTATEUR = "ANNOTATEUR:"

# Les annotateurs qui rendent la mesure circulaire.
#
# **Defaut le plus grave du 2026-10-23, et il a invalide la conclusion
# centrale de la journee.** Les etiquettes de l'echantillon avaient ete
# produites par gb lui-meme. On a donc mesure GK contre le jugement de gb, puis
# gb contre ce meme jugement -- et gb a « gagne » 0,811 contre 0,243.
#
# Ce 0,811 ne mesurait pas la justesse de gb : il mesurait **sa coherence avec
# lui-meme**. `gb_avait_deja_le_bon_avant: 26/37` en est la preuve directe : gb
# designe comme bon le skill qu'il avait charge.
#
# Une etiquette produite par l'une des parties ne peut pas arbitrer entre les
# deux. La commande doit donc le refuser, et non le signaler : un avertissement
# se lit une fois puis s'oublie -- celui sur le biais de selection a disparu du
# raisonnement deux fois dans la meme journee.
ANNOTATEURS_CIRCULAIRES = (
    "gb", "greenbot", "hermes", "agent", "claude", "llm", "modele", "model",
    "ia", "ai", "bot", "moi", "gk",
)
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
    "",
    "Les cinq propositions sont une commodite, pas un menu. Si le bon skill",
    "n'y est pas, ecrivez son nom : c'est la reponse la plus utile du lot,",
    "parce qu'elle est la seule qui ne vienne pas de GK.",
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

# En dessous de deux jetons de contenu, aucune demande n'est jugeable.
#
# Releve par gb le 2026-10-23 : apres le filtre de plateforme il restait 11
# demandes « ok » et 10 qui etaient **la consigne d'annotation elle-meme**,
# tapee dans la conversation. Un « ok » n'appelle aucun skill, et le juger comme
# une demande gonfle la part « aucun ne convient ».
MIN_JETONS_JUGEABLES = 2

MOTIFS_INJECTION = (
    # La consigne d'annotation, quand elle est collee dans la conversation.
    "ecrivez apres",
    "reponse:",
    "echantillon a juger",
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
    if any(m in bas for m in MOTIFS_INJECTION):
        return True
    # Trop court pour appeler un skill : « ok », « oui », « go ».
    from .recherche import jetons as _jetons
    return len(_jetons(texte)) < MIN_JETONS_JUGEABLES


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


def preparer(
    store: Store,
    index: Index,
    taille: int = 50,
    par_page: int = 5,
    par_session: int = 1,
    aveugle: bool = False,
) -> str:
    """Rend l'echantillon, en texte a annoter.

    **Une demande par session, et c'est le reglage qui compte le plus.** Le
    releve du 2026-10-23 a tire 37 demandes de seulement **12 conversations**,
    soit 3,1 par session. Or les demandes d'une meme conversation partagent
    presque toujours le meme bon skill -- 21 skills attendus pour 12 sessions --
    donc elles ne sont pas des observations independantes, et l'effectif reel
    d'une mesure est le nombre de SESSIONS.

    A heure d'annotation egale, une demande par session **triple** l'effectif.

    `aveugle` cache les candidats. Les montrer fait gagner du temps mais ancre :
    l'annotateur choisit dans la liste au lieu de chercher dans la
    bibliotheque. Sans eux la reponse est un nom, plus lente a ecrire et
    independante de GK -- `gk juger` ne sait pas lire un tel fichier, mais
    `gk reetalonner` et `gk ordonnance` recalculent les rangs depuis l'index.
    """
    from .corrections import REQUEST_MAX_CHARS
    from .recherche import jetons

    rows = store.query(
        "SELECT id, session_id, text_truncated, ts, platform FROM requests "
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
    vues: dict[str, int] = {}

    def retenir(r) -> bool:
        """Au plus `par_session` demandes par conversation."""
        sess = r["session_id"] or ""
        if vues.get(sess, 0) >= max(1, par_session):
            return False
        vues[sess] = vues.get(sess, 0) + 1
        return True

    quota = max(1, taille // len(TRANCHES))
    for borne in TRANCHES:
        pris = 0
        for r in par_tranche[borne]:
            if pris >= quota:
                break
            if retenir(r):
                choisis.append(r)
                pris += 1
    # On complete avec les plus recentes si une tranche etait maigre, en
    # gardant la regle d'une demande par session.
    if len(choisis) < taille:
        deja = {id(r) for r in choisis}
        for r in rows:
            if len(choisis) >= taille:
                break
            if id(r) in deja or not retenir(r):
                continue
            choisis.append(r)
    choisis = choisis[:taille]

    lignes = [
        "# Echantillon a juger",
        f"{_MARQUE}{VERSION} -->",
        "",
        f"{MARQUEUR_ANNOTATEUR} ",
        "",
        "Ecrivez votre nom ci-dessus. Ce n'est pas une formalite : une",
        "etiquette produite par l'agent qu'on evalue ne peut pas arbitrer entre",
        "lui et le service. `gk confronter` refusera le fichier.",
        "",
        *_CONSIGNE,
        "",
        "N'ajoutez rien d'autre : le reste du fichier est relu tel quel.",
        "",
        f"{len(choisis)} demandes tirees de {len(vues)} conversations "
        f"distinctes, au plus {max(1, par_session)} par conversation.",
        "",
        "C'est le reglage qui compte le plus : les demandes d'une meme",
        "conversation ont presque toujours le meme bon skill, donc elles ne",
        "comptent pas pour deux. L'effectif reel d'une mesure est le nombre de",
        "conversations.",
        "",
        "Elles sont aussi reparties par longueur, parce qu'une demande courte et",
        "une longue ne posent pas le meme probleme.",
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
        # L'identifiant de la demande, pour que la relecture n'ait pas a
        # l'apparier par son texte. Releve par gb : 16 textes sont dupliques
        # jusqu'a 29 fois, et l'appariement par texte retenait la plus recente
        # -- donc la mauvaise session et le mauvais horodatage.
        lignes.append(f"<!-- demande #{row['id']} -->")
        if len(row["text_truncated"] or "") >= REQUEST_MAX_CHARS:
            # La demande a ete coupee a l'enregistrement : les mots qui
            # identifiaient le skill peuvent manquer, et ce n'est pas un echec
            # de recherche. 61 demandes sur 525 sont dans ce cas.
            lignes.append(
                f"<!-- TRONQUEE a {REQUEST_MAX_CHARS} caracteres : la suite "
                "manque -->"
            )
        if contexte:
            lignes.append(f"<!-- contexte : {len(contexte)} tour(s) precedent(s) -->")
        if aveugle:
            pass  # aucun candidat : la reponse est un nom, independante de GK
        elif not resultats:
            lignes.append("   (aucun skill propose)")
        else:
            for rang, r in enumerate(resultats, start=1):
                lignes.append(
                    f"   {rang}. {r['name']}   [{r['category']}]  "
                    f"score {r['score']}"
                )
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
        out.append({
            "numero": c["numero"], "demande": demande, "attendu": attendu,
            "id": c.get("id"),
        })
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
        if not est_injection(e["demande"], plateformes.get(_clef(e), ""))
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
    courbe_flou = {k: 0 for k in rangs}
    gagnes_par_la_tolerance = 0
    perdus_par_la_tolerance = 0
    distribution: dict[str, int] = {}
    hors = 0
    avec_contexte = 0
    details: list[dict] = []
    for e in etqs:
        session, ts = situe.get(_clef(e), ("", None))
        contexte = (
            contexte_de_session(store, session, avant=ts)
            if session and ts is not None else []
        )
        if contexte:
            avec_contexte += 1
        def _classer(tolerer: bool) -> list[str]:
            if contexte:
                r = index.chercher_avec_contexte(
                    e["demande"], contexte=contexte, limite=max(rangs),
                    tolerer_les_fautes=tolerer,
                )
            else:
                r = index.chercher(
                    e["demande"], limite=max(rangs), tolerer_les_fautes=tolerer,
                )
            return [x["name"] for x in r]

        noms = _classer(False)
        noms_flou = _classer(True)
        rang = noms.index(e["attendu"]) + 1 if e["attendu"] in noms else None
        rang_flou = (
            noms_flou.index(e["attendu"]) + 1
            if e["attendu"] in noms_flou else None
        )
        for k in rangs:
            if rang_flou is not None and rang_flou <= k:
                courbe_flou[k] += 1
        if rang_flou is not None and rang is None:
            gagnes_par_la_tolerance += 1
        if rang is not None and rang_flou is None:
            perdus_par_la_tolerance += 1
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
        # Bras temoin : la tolerance aux fautes de frappe, mesuree puis
        # desactivee -- elle rattrape une demande et en perd deux.
        "courbe_avec_tolerance": {
            str(k): round(courbe_flou[k] / n, 3) for k in rangs
        },
        "gagnes_par_la_tolerance": gagnes_par_la_tolerance,
        "perdus_par_la_tolerance": perdus_par_la_tolerance,
        "distribution_des_rangs": distribution,
        f"hors_des_{max(rangs)}": hors,
        "details": details,
    }


BONUS_CANDIDATS = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0)


def _situer(store: Store) -> tuple[dict, dict]:
    """Session, horodatage et plateforme de chaque demande enregistree.

    Indexe **par identifiant** et par texte. L'appariement par texte est un
    repli pour les echantillons anterieurs : releve par gb le 2026-10-23, 16
    textes sont dupliques jusqu'a 29 fois, et retenir la plus recente donne la
    mauvaise session et le mauvais horodatage. L'impact mesure etait nul sur les
    echantillons du jour, mais le mecanisme est faux.
    """
    rows = store.query(
        "SELECT id, session_id, text_truncated, ts, platform FROM requests "
        "WHERE text_truncated IS NOT NULL AND text_truncated <> '' "
        "ORDER BY ts DESC LIMIT 2000"
    )
    situe: dict[object, tuple[str, float]] = {}
    plats: dict[object, str] = {}
    for r in rows:
        plat = _une_ligne(r["text_truncated"])
        situe[r["id"]] = (r["session_id"] or "", r["ts"])
        plats[r["id"]] = r["platform"] or ""
        situe.setdefault(plat, (r["session_id"] or "", r["ts"]))
        plats.setdefault(plat, r["platform"] or "")
    return situe, plats


def _clef(e: dict) -> object:
    """L'identifiant de la demande s'il est connu, sinon son texte."""
    return e.get("id") if e.get("id") is not None else e["demande"]


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
        session, ts = situe.get(_clef(e), ("", None))
        if ts is None:
            continue
        par_session.setdefault(session, []).append(dict(e, ts=ts, session=session))
    for v in par_session.values():
        v.sort(key=lambda e: e["ts"])

    courbe = {k: 0 for k in rangs}
    n = 0
    rattrapes = 0
    cumul = 0
    for session, demandes in par_session.items():
        memoire: set[str] = set()
        vus_session: set[str] = set()
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
            # Le rattrapage se compte contre le MEME bras sans la memoire,
            # contexte compris. Le comparer a une recherche sans contexte
            # crediterait la memoire du travail du contexte -- defaut de la
            # premiere version, qui annoncait 4 rattrapages alors que le
            # rappel@5 etait identique des deux cotes.
            if contexte:
                temoin = index.chercher_avec_contexte(
                    e["demande"], contexte=contexte, limite=max(rangs)
                )
            else:
                temoin = sans
            noms_temoin = [r["name"] for r in temoin[: max(rangs)]]
            if rang is not None and e["attendu"] not in noms_temoin:
                rattrapes += 1
            n += 1
            # Le cumul de la session : le bon skill a-t-il JAMAIS paru dans les
            # `top` premiers de GK au cours de cette conversation ?
            #
            # C'est le seul equivalent honnete du 0,811 de gb, qui vaut « le bon
            # skill est quelque part dans les neuf charges pendant la session ».
            # Les deux creditent une trouvaille faite a n'importe quel tour.
            vus_session |= {r["name"] for r in avec[:top]}
            if e["attendu"] in vus_session:
                cumul += 1
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
        "cumul_de_session": round(cumul / n, 3) if n else None,
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
    etqs = [
        e for e in etqs
        if not est_injection(e["demande"], plats.get(_clef(e), ""))
    ]
    if not etqs:
        return {"ok": False, "erreur": "aucune etiquette reutilisable"}

    sessions = sorted({situe.get(_clef(e), ("", 0))[0] for e in etqs})
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
        "ajustement": [e for e in etqs if situe.get(_clef(e), ("", 0))[0] in ajust],
        "verification": [e for e in etqs if situe.get(_clef(e), ("", 0))[0] in verif],
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
        # LA comparaison equivalente au 0,811 de gb : le bon skill a-t-il
        # jamais paru dans les `top` premiers de GK au cours de la session ?
        "cumul_sans_memoire": sans_memoire["cumul_de_session"],
        "cumul_avec_memoire": avec_memoire["cumul_de_session"],
        "cible_gb": 0.811,
        "cas_de_verification": sans_memoire["n"],
        "sessions_de_verification": sans_memoire["sessions"],
        "rattrapes_par_la_memoire": avec_memoire["rattrapes_par_la_memoire"],
    }


_TITRE = re.compile(r"^##\s*(\d+)\.\s*(.*)$")
_CANDIDAT = re.compile(r"^\s*(\d+)\.\s+(\S+)\s")
_VERSION = re.compile(re.escape(_MARQUE) + r"(\d+)")
_NOM = re.compile(r"^[A-Za-z][A-Za-z0-9._/-]{2,}$")
_ID = re.compile(r"^<!--\s*demande\s*#(\d+)\s*-->$")


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
    annotateur = ""
    for ligne in (texte or "").splitlines():
        if ligne.strip().startswith(MARQUEUR_ANNOTATEUR):
            annotateur = ligne.split(MARQUEUR_ANNOTATEUR, 1)[1].strip()
            break

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
                "version": version, "id": None, "sans_avis": False,
                "annotateur": annotateur,
            }
            continue
        if courant is None:
            continue
        marque_id = _ID.match(ligne.strip())
        if marque_id:
            courant["id"] = int(marque_id.group(1))
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
            if brut in ("?", "??"):
                # « je ne sais pas » n'est pas « pas annote » : l'un dit que le
                # cas est difficile, l'autre qu'on ne l'a pas regarde.
                courant["sans_avis"] = True
            elif brut.isdigit():
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


def annotateur_de(cas: list[dict]) -> tuple[str, bool]:
    """Qui a annote, et si cela rend toute comparaison circulaire.

    Un fichier sans signature est traite comme circulaire : l'absence de preuve
    d'independance n'est pas une preuve d'independance, et c'est exactement
    l'hypothese qui a coute la journee.
    """
    nom = next((c.get("annotateur") or "" for c in cas if c.get("annotateur")), "")
    plat = "".join(ch for ch in nom.lower() if ch.isalnum() or ch == " ").strip()
    if not plat:
        return "", True
    mots = set(plat.split())
    return nom, bool(mots & set(ANNOTATEURS_CIRCULAIRES))


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
    sans_avis = sum(1 for c in cas if c.get("sans_avis"))
    total = len(annotes)
    if not total:
        return {
            "ok": False,
            "erreur": "aucune reponse annotee",
            "cas_lus": len(cas),
            "non_annotes": len(cas) - sans_avis,
            "sans_avis": sans_avis,
        }

    version = max((c.get("version") or 1) for c in annotes)
    # Un fichier sans candidats affiches ne permet AUCUN rang : tout nom ecrit
    # a la main y serait compte « hors des cinq », et la commande rendrait
    # 0,0 partout. Releve par gb le 2026-10-23 sur l'echantillon aveugle.
    avec_candidats = sum(1 for c in cas if c.get("candidats"))
    if not avec_candidats:
        return {
            "ok": False,
            "erreur": (
                "cet echantillon n'affiche aucun candidat : les rangs y sont "
                "inconnaissables, donc le rappel ne peut pas etre calcule"
            ),
            "cas_lus": len(cas),
            "cas_annotes": total,
            "suite": (
                "les etiquettes restent utilisables : `gk reetalonner` et "
                "`gk ordonnance` recalculent les rangs depuis l'index"
            ),
        }
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
        # Trois etats distincts, et les confondre masquait les cas difficiles :
        # annote, « je ne sais pas », et pas regarde.
        "sans_avis": sans_avis,
        "non_annotes": len(cas) - total - sans_avis,
        "version_echantillon": version,
        "annotateur": annotateur_de(cas)[0] or "(non signe)",
        "circulaire": annotateur_de(cas)[1],
        # Les rangs lus ici sont ceux inscrits AU MOMENT DE L'ANNOTATION. Le
        # classement a change depuis (correction de la fuite de contexte,
        # reindexations) : `gk reetalonner` les recalcule.
        "rangs_au_moment_de_l_annotation": True,
        f"designe_dans_les_{dans_les_n}": dans_cinq,
        "designe_au_rang_1": premier,
        f"designe_hors_des_{dans_les_n}": hors_cinq,
        "aucun_skill_ne_convient": aucun,
        "distribution_des_rangs": distribution,
        # Le taux de CITATION sur toutes les demandes annotees. Celui-la ne
        # depend d'aucune hypothese, et c'est le denominateur que voit un brief :
        # un brief ne sait pas si une demande a une reponse.
        # Deux taux, deux denominateurs, et le nom le dit : la meme sortie en
        # portait deux (23/42 et 23/37) sans que rien ne distingue lequel.
        f"cite_dans_les_{dans_les_n}_par_annote": round(dans_cinq / total, 3),
        "denominateur_par_annote": total,
        "part_premier_quand_cite": (
            round(premier / dans_cinq, 3) if dans_cinq else None
        ),
    }

    servables = dans_cinq + hors_cinq
    if out["circulaire"]:
        # Les chiffres portent alors un autre nom, parce qu'ils mesurent autre
        # chose : l'ACCORD avec l'annotateur, pas la justesse.
        out["denominateur_de_l_accord"] = servables
        if servables:
            out["accord_1_avec_l_annotateur"] = round(premier / servables, 3)
            out[f"accord_{dans_les_n}_avec_l_annotateur"] = round(
                dans_cinq / servables, 3
            )
        out["avertissement"] = (
            f"annotateur « {out['annotateur']} » : ces chiffres mesurent "
            "l'ACCORD de GK avec lui, pas sa justesse. Ils ne peuvent pas "
            "arbitrer entre GK et l'agent, puisque l'une des parties a produit "
            "la reference."
        )
        return out
    if version >= 2:
        # `0` veut dire ce qu'il dit : le denominateur est connu.
        out["demandes_servables"] = servables
        out["denominateur_du_rappel"] = servables
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
