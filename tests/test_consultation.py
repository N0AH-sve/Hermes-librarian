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
    c1 = s.consulter("probleme de factures fournisseurs proforma")
    s.retour(c1["consultation_id"], "1", propositions=["gm-factures"])
    c2 = s.consulter("une demande que rien ne sert vraiment")
    s.retour(c2["consultation_id"], "0", propositions=["gm-factures"])
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
