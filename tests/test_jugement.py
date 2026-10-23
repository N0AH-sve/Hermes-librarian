"""Tests de l'echantillon a juger.

Tout est bloque sur l'absence de verite terrain sur des demandes reelles. Cet
outil l'obtient en une heure d'annotation humaine, donc il doit etre sans
surprise : un format pauvre, une relecture tolerante, et aucun chiffre qui
mente.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk.catalog import Catalog  # noqa: E402
from gk.cli import main as cli_main  # noqa: E402
from gk.jugement import MARQUEUR, juger, preparer, relire  # noqa: E402
from gk.recherche import Index  # noqa: E402
from gk.service import Service  # noqa: E402
from gk.store import Store  # noqa: E402


@pytest.fixture()
def prepare(tmp_path):
    svc = Service.open(tmp_path / "j.db")
    cat = Catalog(svc.store)
    cat.ingest([
        {"path": "greenmusic/gm-factures/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs proforma et port.",
         "category": "greenmusic", "fingerprint": "1"},
        {"path": "greenmusic/gm-marges/SKILL.md", "name": "gm-marges",
         "description": "Marges, prix plancher et commandes a perte.",
         "category": "greenmusic", "fingerprint": "1"},
    ], complete=True)
    cat.store_sections([
        {"path": "greenmusic/gm-factures/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs", "text": '- « proforma fournisseur »'}]},
        {"path": "greenmusic/gm-marges/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs", "text": '- « commandes a perte »'}]},
    ])
    svc.request("s1", "la facture proforma du fournisseur ne colle pas")
    svc.request("s1", "quelles commandes sont a perte")
    svc.request("s2", "une demande sans rapport avec quoi que ce soit")
    yield svc, Index.construire(svc.store)
    svc.close()


# -- la preparation --------------------------------------------------------


def test_l_echantillon_porte_les_demandes_et_les_candidats(prepare):
    svc, index = prepare
    texte = preparer(svc.store, index, taille=10)
    assert "## 1." in texte
    assert MARQUEUR in texte
    assert "gm-factures" in texte or "gm-marges" in texte
    # Les consignes sont dans le fichier : il doit se comprendre seul.
    assert "0` si aucun" in texte and "?` si vous ne savez pas" in texte


def test_l_echantillon_signale_le_contexte_repris(prepare):
    svc, index = prepare
    texte = preparer(svc.store, index, taille=10)
    # La session s1 a deux demandes : la seconde a un contexte.
    assert "contexte :" in texte


# -- la relecture ----------------------------------------------------------


def test_la_relecture_lit_les_reponses(prepare):
    svc, index = prepare
    texte = preparer(svc.store, index, taille=10)
    annote = texte.replace(f"{MARQUEUR} ", f"{MARQUEUR} 1", 1)
    cas = relire(annote)
    assert cas and cas[0]["reponse"] == 1
    assert all(c["demande"] for c in cas)
    # Un cas peut n'avoir AUCUN candidat -- GK ne propose rien pour cette
    # demande -- et c'est une information, pas un defaut. On verifie donc que
    # les candidats sont relus la ou il y en a.
    assert any(c["candidats"] for c in cas)


def test_un_cas_sans_aucun_candidat_est_conserve(prepare):
    """GK ne propose rien pour certaines demandes reelles. Les retirer de
    l'echantillon cacherait precisement les cas qu'il faut voir."""
    svc, index = prepare
    texte = preparer(svc.store, index, taille=10)
    assert "(aucun skill propose)" in texte
    cas = relire(texte)
    assert any(not c["candidats"] for c in cas)


def test_la_relecture_tolere_les_lignes_mal_remplies(prepare):
    svc, index = prepare
    texte = preparer(svc.store, index, taille=10)
    # Une reponse vide, une « ? », une absurde : aucune ne doit etre fatale.
    lignes = texte.splitlines()
    marqueurs = [i for i, l in enumerate(lignes) if l.startswith(MARQUEUR)]
    if len(marqueurs) >= 3:
        lignes[marqueurs[0]] = f"{MARQUEUR} 2"
        lignes[marqueurs[1]] = f"{MARQUEUR} ?"
        lignes[marqueurs[2]] = f"{MARQUEUR} n'importe quoi"
    cas = relire("\n".join(lignes))
    reponses = [c["reponse"] for c in cas]
    assert 2 in reponses
    assert reponses.count(None) >= 2, "les non-reponses restent None"


# -- le jugement -----------------------------------------------------------


def test_le_jugement_rend_le_rappel_reel():
    cas = [
        {"numero": 1, "demande": "a", "candidats": ["x"], "reponse": 1, "brut": "1"},
        {"numero": 2, "demande": "b", "candidats": ["y"], "reponse": 1, "brut": "1"},
        {"numero": 3, "demande": "c", "candidats": ["z"], "reponse": 4, "brut": "4"},
        {"numero": 4, "demande": "d", "candidats": ["w"], "reponse": 0, "brut": "0"},
        {"numero": 5, "demande": "e", "candidats": ["v"], "reponse": None, "brut": "?"},
    ]
    out = juger(cas)
    assert out["cas_annotes"] == 4 and out["non_annotes"] == 1
    assert out["rappel_1_reel"] == 0.5, "deux premiers sur quatre annotes"
    assert out["rappel_5_reel"] == 0.75, "trois designes sur quatre"
    assert out["aucun_ne_convient"] == 1


def test_le_jugement_refuse_un_echantillon_non_annote():
    out = juger([{"numero": 1, "demande": "a", "candidats": [], "reponse": None,
                  "brut": ""}])
    assert out["ok"] is False and "aucune reponse" in out["erreur"]


# -- les commandes ---------------------------------------------------------


def test_gk_echantillon_puis_gk_juger(tmp_path, capsys, monkeypatch):
    svc = Service.open(tmp_path / "e2e.db")
    cat = Catalog(svc.store)
    cat.ingest([{
        "path": "c/gm-un/SKILL.md", "name": "gm-un", "description": "Un skill.",
        "category": "c", "fingerprint": "1",
    }], complete=True)
    cat.store_sections([{
        "path": "c/gm-un/SKILL.md", "fingerprint": "1",
        "sections": [{"heading": "Déclencheurs", "text": '- « zorglub »'}],
    }])
    svc.request("s", "une demande zorglub")
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "e2e.db"))

    assert cli_main(["echantillon", "-n", "5"]) == 0
    texte = capsys.readouterr().out
    assert "## 1." in texte

    annote = texte.replace(f"{MARQUEUR} ", f"{MARQUEUR} 1")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(annote))
    assert cli_main(["--json", "juger"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["rappel_1_reel"] == 1.0
    assert "VRAIES demandes" in out["lecture"][0]


def test_gk_juger_sur_une_entree_qui_n_est_pas_un_echantillon(tmp_path, capsys, monkeypatch):
    Store(tmp_path / "x.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "x.db"))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("bonjour"))
    assert cli_main(["--json", "juger"]) == 1
    assert "ne ressemble pas" in json.loads(capsys.readouterr().out)["erreur"]


def test_gk_juger_signale_les_demandes_sans_reponse_possible(tmp_path, capsys, monkeypatch):
    """Une demande qu'aucun skill ne sert n'est pas un echec de recherche :
    c'est un trou de la bibliotheque, ou une demande qui n'appelle aucun skill.
    Les compter comme des echecs fausserait le jugement."""
    Store(tmp_path / "z.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "z.db"))
    faux = "\n".join(
        f"## {i}. une demande\n   1. un-skill   [c]  score 1\n{MARQUEUR} 0"
        for i in range(1, 5)
    )
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(faux))
    assert cli_main(["--json", "juger"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["part_sans_reponse_possible"] == 1.0
    assert any("trou de la bibliotheque" in l for l in out["lecture"])
