"""Tests de l'optimiseur de prompt, cote serveur.

Ce qui est verifie ici n'est pas « corrige-t-il bien le francais » — un lexique
de 138 mots ne le permettrait pas — mais les trois choses qui decident si
l'optimiseur se laisse utiliser en production :

* il **n'invente pas** de correction (les regles de prudence mordent) ;
* il **n'arrete pas** une demande qui n'est pas contradictoire (les
  suppresseurs mordent) ;
* il **ne repose pas** la meme question en boucle (le dedoublonnage et le
  plafond mordent).

Un detecteur qui attrape peu mais ne se trompe pas se laisse utiliser. Un
detecteur bavard se fait desactiver la premiere semaine, donc les tests de faux
positifs comptent au moins autant que les autres.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "server"))

from gk.coherence import Coherence, Consigne, charger_coherence  # noqa: E402
from gk.optimizer import Lexique, Optimiseur, rendre  # noqa: E402
from gk.regles import charger, lire_sections  # noqa: E402
from gk.service import COOLDOWN_ARRET, MAX_ARRETS_PAR_HEURE, Service  # noqa: E402

MOTIFS = RACINE / "motifs.yaml"
REGLES = RACINE / "optimiseur.yaml"
LEXIQUE = RACINE / "lexique.yaml"


@pytest.fixture
def opt() -> Optimiseur:
    sections, _ = charger(REGLES)
    return Optimiseur(Lexique.load(LEXIQUE), sections.get("triviaux"))


@pytest.fixture
def coh() -> Coherence:
    return charger_coherence(REGLES)


@pytest.fixture
def svc(tmp_path):
    s = Service.open(
        tmp_path / "gk.db", MOTIFS, rules_path=REGLES, lexicon_path=LEXIQUE, share=100
    )
    yield s
    s.close()


# -- le lecteur de regles --------------------------------------------------


def test_lit_les_trois_formes_de_section():
    sections = lire_sections(
        "liste:\n  - un\n  - deux\n"
        "table:\n  cle: valeur\n"
        "mappings:\n  - id: a\n    b: deux\n  - id: c\n"
    )
    assert sections["liste"] == ["un", "deux"]
    assert sections["table"] == {"cle": "valeur"}
    assert sections["mappings"] == [{"id": "a", "b": "deux"}, {"id": "c"}]


def test_une_regexp_a_deux_points_n_est_pas_un_mapping():
    """Le piege du lecteur maison : `(?:a|b)` contient un deux-points."""
    sections = lire_sections('motifs:\n  - "(?:supprime|efface)"\n')
    assert sections["motifs"] == ["(?:supprime|efface)"]


def test_un_fichier_absent_ne_fait_pas_tomber_la_coherence(tmp_path):
    c = Coherence(*charger(tmp_path / "rien.yaml")[:1], source="defauts")
    assert c.source == "defauts"
    assert c.examiner("supprime tout mais garde la base").arret, "les defauts doivent mordre"


def test_un_fichier_illisible_retombe_sur_les_defauts(tmp_path):
    casse = tmp_path / "casse.yaml"
    casse.write_text("contradictions:\n  - id: x\n    a: \"(((\"\n    b: \"*oups\"\n", encoding="utf-8")
    c = charger_coherence(casse)
    # Les deux motifs sont invalides, la regle est donc ignoree sans exception.
    assert c._paires == []
    assert c.examiner("supprime tout mais garde tout").arret is False


def test_l_empreinte_bouge_avec_les_regles(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text('contradictions:\n  - id: x\n    a: "aa"\n    b: "bb"\n', encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text('contradictions:\n  - id: x\n    a: "aa"\n    b: "cc"\n', encoding="utf-8")
    assert charger_coherence(a).digest != charger_coherence(b).digest
    assert charger_coherence(a).digest == charger_coherence(a).digest


# -- la lecture du francais ------------------------------------------------


def test_developpe_les_abreviations(opt):
    lecture = opt.lire("stp fais la maj de la bdd")
    assert "s'il te plait" in lecture.texte
    assert "mise a jour" in lecture.texte
    assert "base de donnees" in lecture.texte


def test_corrige_les_fautes_frequentes(opt):
    assert "par contre" in opt.lire("parcontre il faut le faire").texte


def test_restaure_les_accents_manquants(opt):
    assert "récapitulatif" in opt.lire("fais un recapitulatif").texte


def test_les_corrections_se_chainent(opt):
    """« verfier » donne « verifier », que l'index des accents acheve."""
    assert "vérifier" in opt.lire("il faut verfier la base").texte


def test_garde_la_casse(opt):
    assert opt.lire("Recapitulatif des factures").texte.startswith("Récapitulatif")


@pytest.mark.parametrize(
    "texte",
    [
        "regarde `recapitulatif.py`",          # entre backticks
        'ouvre "recapitulatif" exactement',    # entre guillemets
        "va sur https://x.fr/recapitulatif",   # URL
        "lis /opt/data/recapitulatif",         # chemin
        "ecris a tache@exemple.fr",            # courriel
        "le fichier recapitulatif.md",         # nom de fichier
        "la tache42 et tache2",                # porte un chiffre
        "le SQL et le JSON",                   # sigle
        "appelle maJolieTache",                # majuscule interne
        "le serveur nginx et ovh",             # liste proteges
    ],
)
def test_ce_qui_ne_doit_jamais_etre_corrige(opt, texte):
    assert opt.lire(texte).texte == texte, "l'optimiseur a touche a ce qu'il ne doit pas toucher"


@pytest.mark.parametrize("mot", ["sur", "des", "ou", "ca", "la", "sa"])
def test_les_mots_courts_ne_sont_jamais_accentues(opt, mot):
    """« sur » -> « sûr » et « des » -> « dès » sont les deux facons les plus
    rapides de rendre l'optimiseur nuisible."""
    texte = f"pose le {mot} fichier"
    assert opt.lire(texte).texte == texte


def test_les_participes_ne_sont_pas_inventes(opt):
    """« supprime les logs » ne doit pas devenir « supprimé les logs », et
    « facture » ne doit pas devenir « facturé »."""
    for texte in ("supprime les logs", "la facture de septembre", "il cree le fichier"):
        assert opt.lire(texte).texte == texte


def test_la_distance_1_dort_tant_que_le_lexique_est_petit(opt):
    assert opt.lexique.distance_1_active is False
    # « facteur » est a distance 1 de « facture » : sans le verrou, il serait
    # corrige, et ce serait faux.
    assert opt.lire("le facteur est passe").texte == "le facteur est passe"


def test_la_distance_1_s_allume_avec_le_vocabulaire_des_skills(opt):
    ajoutes = opt.lexique.ajouter_vocabulaire([f"mot{n:05d}" for n in range(2100)])
    assert ajoutes > 2000
    assert opt.lexique.distance_1_active is True


def test_les_messages_triviaux_ne_passent_pas(opt):
    for texte in ("ok", "merci !", "vas-y", "oui", "parfait", "bonjour"):
        assert opt.lire(texte).trivial is True, texte
    assert opt.lire("supprime le fichier de conf").trivial is False


def test_normalise_la_forme_sans_toucher_au_sens(opt):
    lecture = opt.lire("l’adresse    du serveur")
    assert lecture.texte == "l'adresse du serveur"


def test_un_message_tres_long_est_borne(opt):
    lecture = opt.lire("stp " + "x" * 20000)
    assert lecture.texte.endswith("x")
    assert len(lecture.texte) > 20000, "la queue non analysee doit etre conservee telle quelle"


# -- les contradictions ----------------------------------------------------


@pytest.mark.parametrize(
    "texte,regle",
    [
        ("supprime tous les logs mais garde ceux de septembre", "supprimer-vs-garder"),
        ("analyse le code sans rien modifier et corrige les erreurs", "modifier-vs-lecture-seule"),
        ("fais un resume exhaustif en deux lignes minimal", "minimal-vs-exhaustif"),
        ("installe la lib mais sans ajouter de dependance", "installer-vs-rien-installer"),
        ("fais-le sans me demander, mais confirme avant", "demander-vs-ne-pas-demander"),
    ],
)
def test_contradictions_internes(coh, texte, regle):
    verdict = coh.examiner(texte)
    assert verdict.arret is True
    assert regle in [s.regle for s in verdict.signaux]
    assert verdict.question


@pytest.mark.parametrize(
    "texte",
    [
        "supprime tous les logs sauf ceux de septembre",
        "supprime les vieux logs, excepte septembre",
        "d'abord supprime le cache, puis garde une copie de la base",
        "migre de staging vers production",
        "compare production et staging",
        "au lieu de supprimer, garde les logs",
        "fais un resume complet",
        "deploie sur staging",
    ],
)
def test_ce_qui_ne_doit_pas_arreter(coh, texte):
    """Les faux positifs sont ce qui tue la fonctionnalite : « sauf » et
    « d'abord ... puis » resolvent l'opposition."""
    assert coh.examiner(texte).arret is False, texte


def test_le_suppresseur_doit_etre_entre_les_deux(coh):
    """Un « sauf » lointain ne doit pas desarmer une vraie contradiction."""
    verdict = coh.examiner(
        "supprime tous les logs mais garde ceux de septembre. "
        "sinon tout va bien sauf le reste"
    )
    assert verdict.arret is True


def test_valeurs_exclusives(coh):
    verdict = coh.examiner("deploie sur production et sur staging")
    assert verdict.arret is True
    assert [s.genre for s in verdict.signaux] == ["exclusif"]
    assert "production" in verdict.question and "staging" in verdict.question


def test_portee_sans_toucher_a(coh):
    verdict = coh.examiner("sans toucher a la configuration, modifie la configuration du serveur")
    assert verdict.arret is True
    assert "portee" in [s.genre for s in verdict.signaux]


def test_ambiguite_demande_sans_arreter(coh):
    verdict = coh.examiner("deploie maintenant")
    assert verdict.arret is False
    assert verdict.question == "Sur quel environnement : production ou staging ?"


def test_une_ambiguite_ne_se_pose_pas_par_dessus_un_arret(coh):
    """« deploie » sans environnement est ambigu, et « ne deploie pas » dans le
    meme souffle est une contradiction. La seconde seule doit sortir."""
    verdict = coh.examiner("deploie maintenant mais ne deploie pas tout de suite")
    assert verdict.arret is True
    assert "ambiguite" not in [s.genre for s in verdict.signaux]


def test_un_message_vide_ne_dit_rien(coh):
    assert coh.examiner("").arret is False
    assert coh.examiner("   ").signaux == ()


def test_la_signature_est_stable_et_distingue(coh):
    a = coh.examiner("supprime tout mais garde la base")
    b = coh.examiner("supprime les logs mais conserve septembre")
    c = coh.examiner("deploie sur production et sur staging")
    assert a.signature == b.signature, "meme regle, meme question : meme signature"
    assert a.signature != c.signature


# -- les consignes durables et le contexte ---------------------------------


def test_une_consigne_durable_est_retenue(coh):
    consignes = coh.consignes("ne commit jamais sans me demander")
    assert [(c.regle, c.cote) for c in consignes] == [("commiter-vs-pas-commiter", "b")]


def test_une_demande_du_moment_n_est_pas_une_consigne(coh):
    assert coh.consignes("ne commit pas ce fichier") == []


def test_la_double_negation_ne_produit_aucune_consigne(coh):
    """« jamais sans me demander » veut dire le contraire de « sans me
    demander ». Polarite indecidable : on ne retient rien plutot que de se
    tromper de signe trois tours plus tard."""
    regles = [c.regle for c in coh.consignes("ne commit jamais sans me demander")]
    assert "demander-vs-ne-pas-demander" not in regles


def test_contradiction_avec_le_contexte(coh):
    consignes = coh.consignes("ne commit jamais sans me demander")
    verdict = coh.examiner("commit et pousse sur main", consignes)
    assert verdict.arret is True
    assert [s.genre for s in verdict.signaux] == ["contexte"]


def test_un_changement_d_avis_explicite_annule_l_arret(coh):
    consignes = coh.consignes("ne commit jamais sans me demander")
    for suite in ("finalement commit et pousse", "en fait commit maintenant", "oublie, commit"):
        assert coh.examiner(suite, consignes).arret is False, suite


def test_une_consigne_sans_rapport_ne_declenche_rien(coh):
    consignes = coh.consignes("deploie toujours sur staging d'abord")
    assert coh.examiner("fais un resume des factures", consignes).arret is False


# -- le service : les trois garde-fous -------------------------------------


def test_le_bloc_porte_l_arret_et_la_lecture(svc):
    bloc = svc.optimize("s1", "stp suprime tous les logs mais garde ceux de septembre")["block"]
    assert "STOP" in bloc
    assert "Question a poser :" in bloc
    assert "Lu comme" in bloc
    assert "suprime→supprime" in bloc


def test_rien_a_dire_rend_un_bloc_vide(svc):
    assert svc.optimize("s1", "ajoute un test sur le store")["block"] == ""


def test_un_message_trivial_ne_retient_aucune_consigne(svc):
    svc.optimize("s1", "ok")
    assert svc.store.query("SELECT * FROM optimizer_constraints") == []


def test_la_meme_question_n_est_pas_posee_deux_fois(svc):
    premier = svc.optimize("s1", "supprime tous les logs mais garde septembre")["block"]
    second = svc.optimize("s1", "supprime donc les logs mais conserve septembre")["block"]
    assert "STOP" in premier
    assert second == "", "la question a deja ete posee : la reposer boucle"
    assert svc.store.counter("optimize_arrets_etouffes") == 1
    # La raison est comptee a part : un plafond atteint et une question deja
    # posee ne se corrigent pas au meme endroit.
    assert svc.store.counter("optimize_etouffes_deja_pose") == 1
    # L'arret etouffe est quand meme enregistre : la mesure doit voir la
    # contradiction meme quand on choisit de ne pas la reposer.
    lignes = svc.store.query("SELECT emitted FROM optimizer_stops ORDER BY ts")
    assert [l["emitted"] for l in lignes] == [1, 0]


def test_une_autre_contradiction_passe_malgre_le_dedoublonnage(svc):
    svc.optimize("s1", "supprime tous les logs mais garde septembre")
    autre = svc.optimize("s1", "deploie sur production et sur staging")["block"]
    assert "STOP" in autre


def test_une_autre_conversation_n_est_pas_bridee(svc):
    svc.optimize("s1", "supprime tous les logs mais garde septembre")
    assert "STOP" in svc.optimize("s2", "supprime tous les logs mais garde septembre")["block"]


def test_le_plafond_par_conversation(svc, monkeypatch):
    """Une regle trop large doit couter du bruit, pas une conversation."""
    distinctes = [
        "supprime tous les logs mais garde septembre",
        "deploie sur production et sur staging",
        "analyse sans rien modifier et corrige le bug",
        "installe la lib sans ajouter de dependance",
    ]
    poses = [bool("STOP" in svc.optimize("s9", t)["block"]) for t in distinctes]
    assert poses.count(True) == MAX_ARRETS_PAR_HEURE
    assert poses[-1] is False
    assert svc.store.counter("optimize_etouffes_plafond") == 1


def test_le_cooldown_expire(svc):
    svc.optimize("s1", "supprime tous les logs mais garde septembre")
    svc.store.execute(
        "UPDATE optimizer_stops SET ts = ts - ?", (COOLDOWN_ARRET + 60,)
    )
    reprise = svc.optimize("s1", "supprime tous les logs mais garde septembre")["block"]
    assert "STOP" in reprise, "passe le cooldown, la question peut etre reposee"


def test_la_consigne_traverse_les_tours(svc):
    svc.optimize("s3", "ne commit jamais sans me demander")
    svc.optimize("s3", "fais un resume des factures")
    bloc = svc.optimize("s3", "commit et pousse sur main")["block"]
    assert "STOP" in bloc
    assert "consigne posee plus tot" in bloc


def test_une_consigne_reaffirmee_ne_cree_pas_de_doublon(svc):
    svc.optimize("s3", "ne commit jamais sans me demander")
    svc.optimize("s3", "je repete : ne commit jamais sans me demander")
    lignes = svc.store.query("SELECT * FROM optimizer_constraints WHERE session_id = 's3'")
    assert len(lignes) == 1


# -- la part injectee ------------------------------------------------------


def test_part_a_zero_n_injecte_rien_mais_mesure_tout(tmp_path):
    svc = Service.open(tmp_path / "z.db", MOTIFS, rules_path=REGLES, lexicon_path=LEXIQUE, share=0)
    try:
        assert svc.optimize("s1", "supprime tout mais garde la base")["block"] == ""
        assert svc.store.counter("optimize_calls") == 1
        # La consigne est quand meme retenue : le jour ou le bras change, le
        # contexte doit deja etre la.
        svc.optimize("s1", "ne commit jamais sans me demander")
        assert svc.store.query("SELECT * FROM optimizer_constraints")
    finally:
        svc.close()


def test_une_part_illisible_vaut_zero(tmp_path):
    svc = Service.open(
        tmp_path / "x.db", MOTIFS, rules_path=REGLES, lexicon_path=LEXIQUE, share="beaucoup"
    )
    try:
        assert svc.share == 0
    finally:
        svc.close()


def test_le_bras_est_stable_par_conversation(tmp_path):
    svc = Service.open(tmp_path / "ab.db", MOTIFS, rules_path=REGLES, lexicon_path=LEXIQUE, share=50)
    try:
        # Le meme identifiant doit toujours tomber du meme cote : alterner au
        # milieu d'une conversation ne serait pas un bras de mesure.
        for session in ("a", "b", "c", "d"):
            premier = svc._dans_le_bras(session)
            assert all(svc._dans_le_bras(session) is premier for _ in range(5))
        bras = [svc._dans_le_bras(f"s{n}") for n in range(200)]
        assert 60 < sum(bras) < 140, f"repartition suspecte: {sum(bras)}/200"
    finally:
        svc.close()


# -- degradation -----------------------------------------------------------


def test_optimize_se_degrade_sans_lexique(tmp_path):
    svc = Service.open(
        tmp_path / "s.db", MOTIFS, rules_path=REGLES, lexicon_path=tmp_path / "absent.yaml", share=100
    )
    try:
        bloc = svc.optimize("s1", "supprime tous les logs mais garde septembre")["block"]
        assert "STOP" in bloc, "sans lexique, la detection doit continuer"
        assert "Lu comme" not in bloc
    finally:
        svc.close()


def test_rechargement_a_chaud(svc, tmp_path):
    autre = tmp_path / "autre.yaml"
    autre.write_text('contradictions:\n  - id: y\n    a: "alpha"\n    b: "beta"\n', encoding="utf-8")
    avant = svc.coherence.digest
    empreintes = svc.reload_optimizer(autre, LEXIQUE)
    assert empreintes["regles"] != avant
    assert svc.coherence.examiner("alpha et beta").arret is True


def test_health_dit_l_etat_de_l_optimiseur(svc):
    etat = svc.health()["optimiseur"]
    assert etat["actif"] is True
    assert etat["part_injectee"] == 100
    assert etat["contradictions"] > 5
    assert etat["distance_1_active"] is False


def test_le_bloc_est_borne(svc):
    bloc = svc.optimize("s1", "stp " + "fais un recapitulatif des factures " * 40)["block"]
    assert len(bloc) < 800, "le bloc partage le contexte de l'agent avec le brief"


def test_rendre_sans_verdict_ne_donne_que_la_lecture(opt):
    bloc = rendre(opt.lire("stp fais un recapitulatif"))
    assert "STOP" not in bloc
    assert "Lu comme" in bloc


def test_rendre_ne_dit_rien_quand_il_n_y_a_rien_a_dire(opt):
    assert rendre(opt.lire("ajoute un test sur le store")) == ""


# -- retention et effacement -----------------------------------------------


def test_tidy_purge_les_consignes_expirees(svc):
    from gk.optimizer import appliquer_retention

    svc.optimize("s4", "ne commit jamais sans me demander")
    svc.store.execute("UPDATE optimizer_constraints SET expires_at = 1")
    fait = appliquer_retention(svc.store)
    assert fait["consignes_expirees"] == 1
    assert svc.store.query("SELECT * FROM optimizer_constraints") == []


def test_forget_efface_le_texte_sans_perdre_la_mesure(svc, tmp_path, capsys):
    from gk.cli import main as cli_main

    svc.optimize("s5", "supprime tous les logs mais garde septembre")
    svc.store.close()
    code = cli_main(["--json", "--config", str(_config(tmp_path, svc.store.path)), "forget", "--conv", "s5"])
    assert code == 0
    from gk.store import Store

    with Store(svc.store.path) as store:
        ligne = store.one("SELECT extract, question, signature FROM optimizer_stops")
        assert ligne["extract"] is None and ligne["question"] is None
        assert ligne["signature"], "la mesure survit a l'effacement du texte"


def _config(tmp_path: Path, database: Path) -> Path:
    fichier = tmp_path / "config.yaml"
    fichier.write_text(
        f"database: {database}\npatterns: {MOTIFS}\n"
        f"optimizer_rules: {REGLES}\nlexicon: {LEXIQUE}\noptimizer_share: 100\n",
        encoding="utf-8",
    )
    return fichier


# -- la commande de reglage ------------------------------------------------


def test_gk_optimize_n_ecrit_rien(tmp_path, capsys):
    from gk.cli import main as cli_main

    base = tmp_path / "vide.db"
    code = cli_main([
        "--json", "--config", str(_config(tmp_path, base)),
        "optimize", "supprime tous les logs mais garde septembre",
    ])
    assert code == 0
    import json

    sortie = json.loads(capsys.readouterr().out)
    assert sortie["arret"] is True
    assert "STOP" in sortie["bloc"]
    assert not base.exists(), "la commande de reglage ne doit pas creer de base"


def test_gk_optimize_eprouve_le_contexte(tmp_path, capsys):
    from gk.cli import main as cli_main
    import json

    cli_main([
        "--json", "--config", str(_config(tmp_path, tmp_path / "v.db")),
        "optimize", "commit et pousse", "--contexte", "ne commit jamais sans me demander",
    ])
    sortie = json.loads(capsys.readouterr().out)
    assert sortie["arret"] is True
    assert sortie["consignes_du_contexte"][0]["regle"] == "commiter-vs-pas-commiter"
