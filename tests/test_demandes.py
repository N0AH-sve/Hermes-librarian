"""Tests des demandes reelles.

Ce qui est verifie ici est d'abord une **frontiere** : le hook d'entree observe
et n'injecte jamais. Le reste porte sur la retention, parce que c'est du contenu
metier, et sur l'honnetete de l'appariement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk.cli import main as cli_main  # noqa: E402
from gk.corrections import REQUEST_MAX_CHARS, REQUEST_TTL_SECONDS, tidy  # noqa: E402
from gk.service import Service  # noqa: E402
from gk.store import Store, now  # noqa: E402


@pytest.fixture()
def svc(tmp_path):
    s = Service.open(tmp_path / "d.db")
    yield s
    s.close()


# -- la frontiere ----------------------------------------------------------


def test_request_ne_rend_aucun_contenu(svc):
    """La phase d'observation ne doit pas pouvoir devenir une injection par
    accident : la reponse ne porte rien qui puisse etre affiche a l'agent."""
    sortie = svc.request("s", "pourquoi on a paye 40 de port chez Adagio")
    assert sortie["ok"] is True
    assert set(sortie) == {"v", "ok", "request_id", "besoins"}
    assert sortie["besoins"] == ["catalogue_vide"], "le canal retour, rien de plus"


def test_une_demande_vide_ou_absurde_ne_leve_pas(svc):
    for mauvais in (None, 12, "", "   ", {"a": 1}, b"octets"):
        sortie = svc.request("s", mauvais)  # type: ignore[arg-type]
        assert sortie["ok"] is False


# -- la retention ----------------------------------------------------------


def test_le_texte_est_tronque_a_la_longueur_configuree(svc, tmp_path):
    """Releve du 2026-10-23 : sur 240 demandes reelles, mediane 112 caracteres
    mais 98 coupees a 200, soit 41 %. La mesure portait donc sur des demandes
    mutilees. La limite est montee a 1000 et rendue configurable."""
    svc.request("s", "x" * 5000)
    row = svc.store.one("SELECT text_truncated FROM requests")
    assert len(row["text_truncated"]) == REQUEST_MAX_CHARS == 1000

    court = Service.open(tmp_path / "court.db", request_max_chars=120)
    try:
        court.request("s", "y" * 500)
        assert len(court.store.one("SELECT text_truncated FROM requests")[
            "text_truncated"
        ]) == 120
    finally:
        court.close()

    # Un plancher, pour qu'une configuration absurde ne vide pas la mesure.
    plancher = Service.open(tmp_path / "plancher.db", request_max_chars=0)
    try:
        assert plancher.request_max_chars == REQUEST_MAX_CHARS
    finally:
        plancher.close()


def test_la_demande_expire_et_le_compteur_reste(svc):
    svc.request("s", "une demande metier")
    assert int(svc.store.counter("requests_total")) == 1
    svc.store.execute("UPDATE requests SET expires_at = ?", (now() - 1,))
    result = tidy(svc.store)
    assert result["demandes_purgees"] == 1
    assert svc.store.one("SELECT COUNT(*) c FROM requests")["c"] == 0
    assert int(svc.store.counter("requests_total")) == 1, "la mesure survit au texte"


def test_la_duree_de_retention_est_celle_des_preuves(svc):
    svc.request("s", "une demande")
    row = svc.store.one("SELECT ts, expires_at FROM requests")
    assert row["expires_at"] - row["ts"] == pytest.approx(REQUEST_TTL_SECONDS, abs=1)


def test_forget_demandes_efface_le_texte_sans_la_mesure(tmp_path, capsys, monkeypatch):
    s = Service.open(tmp_path / "f.db")
    s.request("s", "une demande metier")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "f.db"))
    assert cli_main(["--json", "forget", "--demandes"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["demandes_supprimees"] == 1
    store = Store(tmp_path / "f.db")
    assert store.one("SELECT COUNT(*) c FROM requests")["c"] == 0
    assert int(store.counter("requests_total")) == 1
    store.close()


def test_forget_par_conversation_couvre_les_demandes(tmp_path, capsys, monkeypatch):
    s = Service.open(tmp_path / "c.db")
    s.request("garder", "demande a garder")
    s.request("effacer", "demande a effacer")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "c.db"))
    assert cli_main(["--json", "forget", "--conv", "effacer"]) == 0
    assert json.loads(capsys.readouterr().out)["demandes_supprimees"] == 1
    store = Store(tmp_path / "c.db")
    restantes = store.query("SELECT session_id FROM requests")
    assert [r["session_id"] for r in restantes] == ["garder"]
    store.close()


# -- la commande -----------------------------------------------------------


def test_gk_demandes_dit_quoi_faire_quand_il_n_y_a_rien(tmp_path, capsys, monkeypatch):
    Store(tmp_path / "v.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "v.db"))
    assert cli_main(["--json", "demandes"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["demandes_observees"] == 0
    assert "gk-bridge.jsonl" in out["remarque"], "ou chercher si ca reste a zero"


def test_gk_demandes_apparie_le_skill_charge(tmp_path, capsys, monkeypatch):
    s = Service.open(tmp_path / "a.db")
    s.request("s1", "controler les factures du fournisseur", platform="discord")
    s.skill_lifecycle("gm-supplier-invoice-control", session_id="s1", action="use")
    s.request("s2", "une demande restee sans skill", platform="discord")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "a.db"))
    assert cli_main(["--json", "demandes"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["demandes_suivies_d_un_skill_sous_2min"] == 1
    assert out["par_plateforme"] == {"discord": 2}
    # Le compte par SESSION est celui qui porte la question.
    assert out["sessions_observees"] == 2
    assert out["sessions_sans_aucun_skill"] == 1
    assert out["demandes_dans_une_session_sans_skill"] == 1
    # L'appariement est une correlation, et la commande le dit.
    assert "correlation et non une verite" in out["remarque_appariement"]


def test_un_skill_charge_plus_tot_dans_la_session_compte_quand_meme(
    tmp_path, capsys, monkeypatch
):
    """Le defaut du premier releve : la fenetre de deux minutes ne regarde que
    l'APRES, donc au dixieme message d'une conversation le skill charge au debut
    etait invisible. Six demandes en milieu de conversation avaient donne 0, ce
    qui se lisait comme \u00ab gb ne charge rien \u00bb."""
    from gk.store import Store as _Store

    s = Service.open(tmp_path / "t.db")
    s.skill_lifecycle("gm-un-skill", session_id="s1", action="use")
    store = s.store
    # La demande arrive bien APRES le chargement, et hors fenetre.
    store.execute("UPDATE skill_uses SET ts = ?", (now() - 3600,))
    s.request("s1", "une question de suivi")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "t.db"))
    assert cli_main(["--json", "demandes"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["demandes_suivies_d_un_skill_sous_2min"] == 0, "hors fenetre"
    assert out["sessions_sans_aucun_skill"] == 0, "mais la session a bien charge"
    assert out["demandes_dans_une_session_sans_skill"] == 0
    assert _Store is not None


def test_gk_demandes_ne_montre_le_texte_que_sur_demande(tmp_path, capsys, monkeypatch):
    s = Service.open(tmp_path / "e.db")
    s.request("s", "du contenu metier")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "e.db"))
    assert cli_main(["--json", "demandes"]) == 0
    assert "exemples" not in json.loads(capsys.readouterr().out)
    assert cli_main(["--json", "demandes", "--exemples", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["exemples"] == ["du contenu metier"]


# -- le contexte lu depuis les demandes reelles ----------------------------


def test_le_contexte_de_session_reprend_les_tours_precedents(svc):
    """Lu depuis les demandes deja enregistrees : rien de nouveau n'est
    collecte pour le contexte."""
    from gk.recherche import contexte_de_session

    svc.request("s1", "premiere question sur les factures")
    svc.request("s1", "deuxieme question sur le port")
    svc.request("s1", "troisieme question, la courante")
    svc.request("autre", "une session voisine")
    contexte = contexte_de_session(svc.store, "s1")
    # La plus recente est la demande courante : elle n'est pas son propre contexte.
    assert "troisieme question, la courante" not in contexte
    assert "deuxieme question sur le port" in contexte
    assert "une session voisine" not in contexte


def test_le_contexte_est_borne_en_nombre_et_en_duree(svc):
    from gk.recherche import contexte_de_session

    for i in range(8):
        svc.request("s", f"question numero {i}")
    assert len(contexte_de_session(svc.store, "s", tours=3)) == 3
    # Hors fenetre : une conversation de deux heures n'oriente plus la question.
    svc.store.execute("UPDATE requests SET ts = ts - 7200 WHERE text_truncated LIKE '%numero 0%'")
    contexte = contexte_de_session(svc.store, "s", tours=8, fenetre=600)
    assert not any("numero 0" in c for c in contexte)


def test_sans_session_il_n_y_a_pas_de_contexte(svc):
    from gk.recherche import contexte_de_session

    svc.request("s", "une demande")
    assert contexte_de_session(svc.store, "") == []
    assert contexte_de_session(svc.store, "inconnue") == []
    # Une seule demande dans la session : elle est la courante, pas un contexte.
    assert contexte_de_session(svc.store, "s") == []


def test_gk_demandes_estime_le_taux_de_suite(tmp_path, capsys, monkeypatch):
    """Le seuil de rentabilite du contexte est a 11 % de tours restant sur le
    sujet : c'est le taux reel qui decide du solde."""
    s = Service.open(tmp_path / "ts.db")
    s.request("stable", "une question")
    s.skill_lifecycle("gm-un", session_id="stable", action="use")
    s.skill_lifecycle("gm-un", session_id="stable", action="use")
    s.request("bascule", "une autre question")
    s.skill_lifecycle("gm-un", session_id="bascule", action="use")
    s.skill_lifecycle("gm-deux", session_id="bascule", action="use")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "ts.db"))
    assert cli_main(["--json", "demandes"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sessions_avec_skills"] == 2
    assert out["sessions_a_un_seul_skill"] == 1
    assert out["taux_de_suite_estime"] == 0.5
    assert "11 %" in out["remarque_bascule"]


# -- la transferabilite du seuil ------------------------------------------


def test_le_seuil_se_mesure_sur_les_demandes_reelles_sans_etiquette(
    tmp_path, capsys, monkeypatch
):
    """Le seuil retenu au banc est `couverture >= 0,6`. Les phrases citees font
    2 a 6 mots, les demandes reelles une vingtaine : un seuil mesure sur les
    secondes ne veut pas dire la meme chose sur les premieres. La question est
    la transferabilite, et elle se mesure SANS verite terrain."""
    from gk.catalog import Catalog

    s = Service.open(tmp_path / "tr.db")
    cat = Catalog(s.store)
    cat.ingest([{
        "path": "greenmusic/gm-factures/SKILL.md", "name": "gm-factures",
        "description": "Factures fournisseurs proforma.",
        "category": "greenmusic", "fingerprint": "1",
    }], complete=True)
    cat.store_sections([{
        "path": "greenmusic/gm-factures/SKILL.md", "fingerprint": "1",
        "sections": [{"heading": "Déclencheurs", "text": '- « proforma fournisseur »'}],
    }])
    # Une demande courte, bien couverte, et une longue, mal couverte.
    s.request("s1", "proforma fournisseur")
    s.request("s2", "bonjour je voudrais comprendre pourquoi la facture du "
                    "fournisseur ne correspond pas au bon de commande envoye "
                    "la semaine derniere par le service achat")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "tr.db"))
    assert cli_main(["--json", "seuil", "--reel"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["demandes_reelles"] == 2
    # La demande longue porte beaucoup plus de jetons que le corpus.
    assert out["jetons_mediane_reelle"] >= out["jetons_mediane_du_corpus"]
    # Une seule des deux franchit un seuil de couverture eleve.
    assert out["passeraient_couverture_0_6"] <= 0.5
    assert "TRANSFERABILITE" in out["lecture"][0]


def test_seuil_reel_sans_demande(tmp_path, capsys, monkeypatch):
    from gk.catalog import Catalog

    s = Service.open(tmp_path / "vide.db")
    Catalog(s.store).ingest([{
        "path": "c/a/SKILL.md", "name": "a", "description": "Un skill.",
        "category": "c", "fingerprint": "1",
    }], complete=True)
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "vide.db"))
    assert cli_main(["--json", "seuil", "--reel"]) == 1
    assert "aucune demande reelle" in json.loads(capsys.readouterr().out)["erreur"]


def test_le_contexte_retrospectif_ne_lit_pas_le_futur(svc):
    """Defaut releve par gb le 2026-10-23, et il faussait toute evaluation.

    Sans ancrage, `contexte_de_session` part de `MAX(ts)` de la session : juste
    en direct, puisque la demande courante y est la plus recente, mais faux pour
    une demande du passe, ou il renvoie les DERNIERS tours -- donc le futur de
    la demande jugee.
    """
    from gk.recherche import contexte_de_session

    svc.request("s1", "premier tour")
    svc.request("s1", "deuxieme tour")
    svc.request("s1", "troisieme tour")
    svc.request("s1", "quatrieme tour")
    rows = svc.store.query("SELECT text_truncated, ts FROM requests ORDER BY ts")
    ts = {r["text_truncated"]: r["ts"] for r in rows}

    # Juge en direct sur le dernier tour : les trois precedents, sans le futur
    # (il n'y en a pas).
    en_direct = contexte_de_session(svc.store, "s1")
    assert "troisieme tour" in en_direct
    assert "quatrieme tour" not in en_direct, "pas son propre contexte"

    # Juge le DEUXIEME tour, retrospectivement.
    retro = contexte_de_session(svc.store, "s1", avant=ts["deuxieme tour"])
    assert retro == ["premier tour"], retro
    assert "troisieme tour" not in retro, "le futur ne doit pas fuir"
    assert "quatrieme tour" not in retro, "le futur ne doit pas fuir"


def test_le_contexte_retrospectif_respecte_la_fenetre(svc):
    """La borne basse reste relative a la demande jugee, pas a la session."""
    from gk.recherche import contexte_de_session

    svc.request("s1", "tres ancien")
    svc.request("s1", "juste avant")
    svc.request("s1", "la demande jugee")
    rows = svc.store.query("SELECT text_truncated, ts FROM requests ORDER BY ts")
    ts = {r["text_truncated"]: r["ts"] for r in rows}
    svc.store.execute(
        "UPDATE requests SET ts = ? WHERE text_truncated = ?",
        (ts["la demande jugee"] - 100_000, "tres ancien"),
    )
    retro = contexte_de_session(
        svc.store, "s1", avant=ts["la demande jugee"], fenetre=1800
    )
    assert retro == ["juste avant"], retro


def test_l_echantillon_n_injecte_pas_le_futur_dans_ses_candidats(svc):
    """Consequence de la fuite : les candidats de `gk echantillon` etaient
    classes avec les tours suivants de la conversation.

    C'est ce qui invalide le rappel mesure avant la correction.
    """
    from gk.catalog import Catalog
    from gk.jugement import preparer
    from gk.recherche import Index

    Catalog(svc.store).ingest([
        {"path": "a/futur/SKILL.md", "name": "gm-futur",
         "description": "Marges et prix plancher.", "category": "c",
         "fingerprint": "1"},
        {"path": "a/neutre/SKILL.md", "name": "gm-neutre",
         "description": "Expedition et transporteurs.", "category": "c",
         "fingerprint": "1"},
    ], complete=True)
    # Le premier tour ne parle de rien ; le second parle de marges.
    # Deux jetons au moins : en dessous, la demande n'est pas jugeable.
    svc.request("s1", "bonjour tout le monde")
    svc.request("s1", "les marges et le prix plancher du catalogue")
    index = Index.construire(svc.store)
    texte = preparer(svc.store, index, taille=10)
    # Le bloc du premier tour ne doit pas etre classe grace au second.
    bloc = texte.split("## ")
    premier = [b for b in bloc if b.startswith("1. ") or b.startswith("2. ")]
    assert premier, "l'echantillon porte bien les demandes"
    # Aucun commentaire de contexte sur une demande qui n'a pas de passe.
    tete = [b for b in bloc if "bonjour" in b]
    assert tete and "contexte :" not in tete[0], tete
