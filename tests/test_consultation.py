"""Tests de la consultation demandee par un humain -- la commande `/gk`.

Le refus de l'etape 2 -- ne pas donner a l'agent un outil qu'il appellerait --
portait sur l'AGENT : une machine qui oublie de verifier oublie aussi de
demander. Un humain qui tape `/gk` n'oublie rien, il a decide. Le refus ne
s'applique pas, et il avait ete ecrit pour l'autre cas.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk.catalog import Catalog  # noqa: E402
from gk.cli import main as cli_main  # noqa: E402
from gk.service import Service  # noqa: E402


@pytest.fixture()
def svc(tmp_path):
    s = Service.open(tmp_path / "c.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma et frais de port.",
         "category": "gm", "fingerprint": "1"},
        {"path": "a/m/SKILL.md", "name": "gm-marges",
         "description": "Marges, prix plancher et commandes a perte.",
         "category": "gm", "fingerprint": "1"},
    ], complete=True)
    yield s
    s.close()


def test_une_consultation_rend_les_propositions_avec_leurs_descriptions(svc):
    """Un nom seul ne dit rien de ce que le skill couvre : la description est
    ce qui permet de choisir."""
    out = svc.consulter("probleme de factures fournisseurs proforma")
    assert out["ok"] is True
    assert out["propositions"][0]["name"] == "gm-factures"
    assert "Factures fournisseurs" in out["propositions"][0]["description"]
    assert out["consultation_id"]


def test_une_consultation_laisse_une_trace_pour_l_etiqueter(svc):
    """Sans ce lien, « c'etait le bon » ne designerait rien."""
    out = svc.consulter("les marges et le prix plancher")
    row = svc.store.one(
        "SELECT note, outcome, source FROM reports WHERE id = ?",
        (out["consultation_id"],),
    )
    assert row["outcome"] == "consultation"
    assert row["source"] == "hook", "la consultation n'est pas une notation humaine"
    assert "marges" in row["note"]


def test_un_retour_est_une_etiquette_humaine(svc):
    """`source='human'` ne se melange jamais avec les compteurs automatiques :
    l'etape 4 fait des uns la mesure et des autres la calibration."""
    c = svc.consulter("probleme de factures fournisseurs proforma")
    noms = [p["name"] for p in c["propositions"]]
    out = svc.retour(c["consultation_id"], "1", propositions=noms)
    assert out["ok"] is True and out["etiquette"] == "gm-factures"
    row = svc.store.one(
        "SELECT skill_name, outcome, source, brief_id FROM reports WHERE id = ?",
        (out["report_id"],),
    )
    assert row["source"] == "human"
    assert row["outcome"] == "bon_skill"
    assert row["brief_id"] == c["consultation_id"], "rattache a sa consultation"


def test_un_retour_accepte_un_nom_hors_des_propositions(svc):
    """C'est la reponse la plus utile du lot : la seule qui ne vienne pas de
    GK."""
    c = svc.consulter("une demande qui ne ressemble a rien")
    out = svc.retour(c["consultation_id"], "gm-ailleurs", propositions=[])
    assert out["ok"] is True and out["etiquette"] == "gm-ailleurs"


def test_un_retour_zero_dit_qu_aucun_skill_ne_convient(svc):
    """Et ce n'est pas un echec de recherche : c'est un trou de la
    bibliotheque, ou une demande qui n'appelle aucun skill."""
    c = svc.consulter("probleme de factures fournisseurs proforma")
    out = svc.retour(c["consultation_id"], "0", propositions=["gm-factures"])
    assert out["ok"] is True and out["etiquette"] is None
    row = svc.store.one(
        "SELECT outcome FROM reports WHERE id = ?", (out["report_id"],)
    )
    assert row["outcome"] == "aucun_ne_convient"


def test_un_rang_hors_des_propositions_est_refuse(svc):
    """Sinon « 7 » sur une liste de cinq enregistrerait une etiquette vide."""
    c = svc.consulter("probleme de factures fournisseurs proforma")
    out = svc.retour(c["consultation_id"], "7", propositions=["gm-factures"])
    assert out["ok"] is False and "hors_des_propositions" in out["erreur"]


def test_une_consultation_sur_catalogue_vide_le_dit(tmp_path):
    s = Service.open(tmp_path / "vide.db")
    out = s.consulter("une demande")
    s.close()
    assert out["ok"] is False and out["erreur"] == "catalogue_vide"


# -- l'export, et c'est le vrai gain ---------------------------------------


def test_les_retours_s_exportent_au_format_de_l_echantillon(
    tmp_path, capsys, monkeypatch
):
    """Le vrai gain de `/gk` : les etiquettes s'accumulent au fil du travail,
    sans seance d'annotation.

    L'export passe par le format de l'echantillon pour que `gk confronter` et
    `gk reetalonner` les lisent sans traitement special.
    """
    s = Service.open(tmp_path / "x.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma.", "category": "gm",
         "fingerprint": "1"},
    ], complete=True)
    # Avec une session : ce sont des demandes de collegues, pas des essais en
    # ligne de commande, que l'export ecarte.
    c1 = s.consulter("probleme de factures fournisseurs proforma",
                     session_id="20261023_a")
    s.retour(c1["consultation_id"], "1", session_id="20261023_a",
             propositions=["gm-factures"])
    c2 = s.consulter("une demande que rien ne sert vraiment",
                     session_id="20261023_b")
    s.retour(c2["consultation_id"], "0", session_id="20261023_b",
             propositions=["gm-factures"])
    s.close()

    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "x.db"))
    assert cli_main(["etiquettes", "--annotateur", "Noah"]) == 0
    texte = capsys.readouterr().out
    assert "ANNOTATEUR: Noah" in texte
    assert "## 1. probleme de factures fournisseurs proforma" in texte
    assert "REPONSE: gm-factures" in texte
    assert "REPONSE: 0" in texte

    # Et la chaine de mesure le relit sans rien de plus.
    from gk.jugement import etiquettes, relire
    cas = relire(texte)
    assert len(cas) == 2
    assert etiquettes(cas)[0]["attendu"] == "gm-factures"


def test_l_export_sans_etiquette_le_dit(tmp_path, capsys, monkeypatch):
    Service.open(tmp_path / "v.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "v.db"))
    assert cli_main(["--json", "etiquettes"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert "aucune etiquette humaine" in out["erreur"]


def test_gk_consulter_dit_comment_etiqueter(tmp_path, capsys, monkeypatch):
    s = Service.open(tmp_path / "y.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma.", "category": "gm",
         "fingerprint": "1"},
    ], complete=True)
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "y.db"))
    assert cli_main(["--json", "consulter", "factures fournisseurs"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert any("gk retour --consultation" in l for l in out["lecture"])
    assert any("plus dures que la moyenne" in l for l in out["lecture"])
    assert any("Rien n'est injecte" in l for l in out["lecture"])


def test_les_consultations_se_listent_avec_leur_identifiant(
    tmp_path, capsys, monkeypatch
):
    """Sans cette commande la boucle est incomplete : on ne peut pas etiqueter
    une consultation dont on ne connait pas le numero."""
    s = Service.open(tmp_path / "l.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma.", "category": "gm",
         "fingerprint": "1"},
    ], complete=True)
    c1 = s.consulter("probleme de factures fournisseurs proforma")
    c2 = s.consulter("une autre question sur les factures proforma")
    s.retour(c1["consultation_id"], "1", propositions=["gm-factures"])
    s.close()

    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "l.db"))
    assert cli_main(["--json", "consultations"]) == 0
    out = json.loads(capsys.readouterr().out)
    par_id = {c["consultation"]: c for c in out["consultations"]}
    assert par_id[c1["consultation_id"]]["retour"] == "gm-factures"
    assert par_id[c2["consultation_id"]]["retour"] is None
    assert out["sans_retour"] == 1
    assert any("etiquette humaine perdue" in l for l in out["lecture"])


def test_un_retour_aucun_ne_convient_se_lit_dans_la_liste(
    tmp_path, capsys, monkeypatch
):
    s = Service.open(tmp_path / "l2.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs.", "category": "gm",
         "fingerprint": "1"},
    ], complete=True)
    c = s.consulter("probleme de factures fournisseurs")
    s.retour(c["consultation_id"], "0", propositions=["gm-factures"])
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "l2.db"))
    assert cli_main(["--json", "consultations"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["consultations"][0]["retour"] == "aucun ne convient"
    assert out["sans_retour"] == 0


def test_sans_consultation_la_commande_renvoie_vers_discord(
    tmp_path, capsys, monkeypatch
):
    Service.open(tmp_path / "l3.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "l3.db"))
    assert cli_main(["--json", "consultations"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert "!gk" in out["suite"]


def test_un_essai_en_ligne_de_commande_est_distingue(tmp_path, capsys, monkeypatch):
    """Une consultation lancee en ligne de commande n'a pas de session : c'est
    un essai, pas une demande de collegue.

    Les deux etaient indistinguables, donc etiqueter la liste entiere mettait
    des doublons d'essai dans le corpus.
    """
    s = Service.open(tmp_path / "o.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma.", "category": "gm",
         "fingerprint": "1"},
    ], complete=True)
    essai = s.consulter("probleme de factures fournisseurs proforma")
    vraie = s.consulter(
        "probleme de factures fournisseurs proforma", session_id="20261023_x"
    )
    s.retour(essai["consultation_id"], "1", propositions=["gm-factures"])
    s.retour(vraie["consultation_id"], "1", session_id="20261023_x",
             propositions=["gm-factures"])
    s.close()

    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "o.db"))
    assert cli_main(["--json", "consultations"]) == 0
    out = json.loads(capsys.readouterr().out)
    par_id = {c["consultation"]: c for c in out["consultations"]}
    assert par_id[essai["consultation_id"]]["origine"] == "essai_cli"
    assert par_id[vraie["consultation_id"]]["origine"] == "agent"
    assert out["essais_cli"] == 1

    # Et l'export n'en garde qu'une : celle du collegue.
    assert cli_main(["etiquettes", "--annotateur", "Noah"]) == 0
    texte = capsys.readouterr().out
    assert texte.count("REPONSE:") == 1, texte


# -- les verdicts d'un collegue --------------------------------------------


def test_un_collegue_juge_la_reponse_pas_le_classement(svc):
    """Defaut releve par GM en relisant la note de consignes : un collegue ne
    sait pas quel skill a ete charge.

    Il ne le voit pas, et il y en a 186. Lui demander d'en nommer un n'avait
    aucun sens. Il juge donc ce qu'il a sous les yeux -- la reponse de gb.
    """
    c = svc.consulter("probleme de factures fournisseurs proforma",
                      session_id="s1")
    for mot, attendu in (("servi", "servi"), ("a-cote", "a_cote"),
                         ("inconnu", "inconnu"), ("oui", "servi"),
                         ("non", "a_cote"), ("?", "inconnu")):
        out = svc.retour(c["consultation_id"], mot, session_id="s1")
        assert out["ok"] is True, mot
        assert out["verdict"] == attendu, mot
        assert out["etiquette"] is None, "aucun skill n'est designe"


def test_un_verdict_dit_ce_qu_il_mesure(svc):
    """`servi` porte la reponse de bout en bout, un rang porte la justesse de
    GK. Les additionner produirait un chiffre vide."""
    c = svc.consulter("probleme de factures fournisseurs proforma",
                      session_id="s1")
    bout = svc.retour(c["consultation_id"], "servi", session_id="s1")
    assert "bout en bout" in bout["porte"]
    rang = svc.retour(c["consultation_id"], "1", session_id="s1",
                      propositions=["gm-factures"])
    assert "rang de GK" in rang["porte"] and "justesse" in rang["porte"]


def test_la_consultation_garde_toute_la_liste_des_propositions(svc):
    """C'est elle qui permet de retrouver le rang quand un collegue dit
    seulement « ca m'a servi ».

    Gardee dans `reports` et non dans `briefs` -- qui a une colonne `skills`
    faite pour ca -- parce que les briefs expirent a sept jours et qu'une
    etiquette humaine est la donnee la plus couteuse du projet.
    """
    c = svc.consulter("factures fournisseurs proforma et marges",
                      session_id="s1")
    row = svc.store.one(
        "SELECT skill_name FROM reports WHERE id = ?", (c["consultation_id"],)
    )
    noms = row["skill_name"].split(",")
    assert len(noms) == len(c["propositions"]) >= 2
    assert noms == [p["name"] for p in c["propositions"]]


def test_les_verdicts_rendent_le_triplet_complet(tmp_path, capsys, monkeypatch):
    """La demande, ce que GK proposait, ce que gb a charge : c'est le croisement
    qui donne le rang sans rien demander au collegue."""
    s = Service.open(tmp_path / "v.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma et port.",
         "category": "gm", "fingerprint": "1"},
        {"path": "a/m/SKILL.md", "name": "gm-marges",
         "description": "Marges et prix plancher.", "category": "gm",
         "fingerprint": "1"},
    ], complete=True)
    c = s.consulter("probleme de factures fournisseurs proforma",
                    session_id="s1")
    ts = s.store.one("SELECT ts FROM reports WHERE id = ?",
                     (c["consultation_id"],))["ts"]
    s.store.execute(
        "INSERT INTO skill_uses (ts, session_id, skill_name, action, expires_at) "
        "VALUES (?, 's1', 'gm-factures', 'loaded', ?)", (ts + 5, ts + 1e9)
    )
    s.retour(c["consultation_id"], "servi", session_id="s1")
    s.close()

    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "v.db"))
    assert cli_main(["--json", "verdicts"]) == 0
    out = json.loads(capsys.readouterr().out)
    v = out["verdicts"][0]
    assert v["verdict"] == "servi"
    assert "gm-factures" in v["gk_proposait"]
    assert v["gb_a_charge"] == ["gm-factures"]
    assert out["de_bout_en_bout"]["servi"] == 1
    assert any("ne mesurent PAS la meme chose" in l for l in out["lecture"])
    assert any("CORRELATION" in l for l in out["lecture"])


def test_les_deux_familles_de_verdicts_ne_s_additionnent_pas(
    tmp_path, capsys, monkeypatch
):
    s = Service.open(tmp_path / "v2.db")
    Catalog(s.store).ingest([
        {"path": "a/f/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma.", "category": "gm",
         "fingerprint": "1"},
    ], complete=True)
    c1 = s.consulter("probleme de factures fournisseurs", session_id="s1")
    s.retour(c1["consultation_id"], "servi", session_id="s1")
    c2 = s.consulter("autre probleme de factures fournisseurs", session_id="s2")
    s.retour(c2["consultation_id"], "1", session_id="s2",
             propositions=["gm-factures"])
    c3 = s.consulter("troisieme probleme de factures", session_id="s3")
    s.retour(c3["consultation_id"], "rien", session_id="s3")
    s.close()

    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "v2.db"))
    assert cli_main(["--json", "verdicts"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["de_bout_en_bout"]["servi"] == 1
    assert out["rangs_quand_le_skill_est_designe"]["1"] == 1
    assert out["trous_de_bibliotheque"] == 1
