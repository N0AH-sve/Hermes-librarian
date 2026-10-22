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
from gk.corrections import REQUEST_TTL_SECONDS, tidy  # noqa: E402
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


def test_le_texte_est_tronque_a_200_caracteres(svc):
    svc.request("s", "x" * 500)
    row = svc.store.one("SELECT text_truncated FROM requests")
    assert len(row["text_truncated"]) == 200


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
    assert out["demandes_suivies_d_un_skill"] == 1
    assert out["demandes_sans_aucun_skill"] == 1
    assert out["par_plateforme"] == {"discord": 2}
    # L'appariement est une correlation, et la commande le dit.
    assert "correlation, pas une verite" in out["remarque_appariement"]


def test_gk_demandes_ne_montre_le_texte_que_sur_demande(tmp_path, capsys, monkeypatch):
    s = Service.open(tmp_path / "e.db")
    s.request("s", "du contenu metier")
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "e.db"))
    assert cli_main(["--json", "demandes"]) == 0
    assert "exemples" not in json.loads(capsys.readouterr().out)
    assert cli_main(["--json", "demandes", "--exemples", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["exemples"] == ["du contenu metier"]
