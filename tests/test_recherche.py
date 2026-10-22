"""Tests de la recherche.

Deux proprietes portent tout le reste, et leur violation serait invisible :

* **l'IDF** — `prestashop` est dans beaucoup de skills, `proforma` dans un seul.
  Les compter pareil ferait remonter le mauvais skill a chaque demande qui
  nomme un outil courant ;
* **l'exclusion de la phrase testee** — mesurer sur les lignes citees sans les
  retirer de l'index de leur propre skill serait circulaire, et rendrait un
  chiffre flatteur qui ne veut rien dire.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk.catalog import Catalog  # noqa: E402
from gk.cli import main as cli_main  # noqa: E402
from gk.recherche import Index, jetons  # noqa: E402
from gk.store import Store  # noqa: E402


def peupler(store: Store, skills: dict) -> None:
    """`{nom: (description, [(titre, texte)])}`."""
    cat = Catalog(store)
    cat.ingest(
        [
            {"path": f"cat/{nom}/SKILL.md", "name": nom, "description": desc,
             "category": "cat", "fingerprint": "1"}
            for nom, (desc, _) in skills.items()
        ],
        complete=True,
    )
    cat.store_sections([
        {"path": f"cat/{nom}/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": h, "text": t} for h, t in sections]}
        for nom, (_, sections) in skills.items()
    ])


BIBLIOTHEQUE = {
    "gm-supplier-invoice-control": (
        "Controler les factures fournisseurs : PA et frais de port, proforma et facture finale.",
        [("Déclencheur :", '- « controle des factures fournisseurs »\n- « point dettes »')],
    ),
    "gm-presta-catalogue": (
        "Fiches produits PrestaShop : creation, categories, prix.",
        [("Déclencheur :", '- « creer une fiche produit »\n- « categorie prestashop »')],
    ),
    "gm-compta-audit": (
        "Audit comptable PrestaShop et Pennylane.",
        [("Déclencheur :", '- « audit compta »')],
    ),
}


@pytest.fixture()
def index(tmp_path):
    store = Store(tmp_path / "r.db")
    peupler(store, BIBLIOTHEQUE)
    yield Index.construire(store)
    store.close()


# -- la tokenisation --------------------------------------------------------


def test_les_jetons_plient_les_accents_et_retirent_les_mots_vides():
    assert jetons("Contrôler LES factures du fournisseur") == [
        "controler", "factures", "fournisseur"
    ]


def test_les_sigles_metier_de_deux_lettres_survivent():
    """« PA », « IV », « PRE » sont des termes du metier : une longueur
    minimale de trois les aurait effaces."""
    assert "pa" in jetons("le PA attendu")
    assert "iv" in jetons("facture IV")


# -- l'IDF ------------------------------------------------------------------


def test_un_mot_present_partout_pese_moins_qu_un_mot_rare(index):
    """Sans ce facteur, toute demande nommant un outil courant remonterait le
    mauvais skill."""
    assert index.df["prestashop"] == 2
    assert index.df["proforma"] == 1
    assert index.idf("proforma") > index.idf("prestashop")


def test_un_mot_rare_designe_le_bon_skill(index):
    resultats = index.chercher("proforma")
    assert resultats[0]["name"] == "gm-supplier-invoice-control"


# -- les champs et leurs poids ---------------------------------------------


def test_une_citation_pese_plus_que_la_prose(index):
    resultats = index.chercher("point dettes")
    assert resultats[0]["name"] == "gm-supplier-invoice-control"
    assert "exemples" in resultats[0]["detail"]


def test_le_detail_dit_quel_champ_a_decide(index):
    resultats = index.chercher("factures fournisseurs")
    detail = resultats[0]["detail"]
    assert set(detail) <= {"name", "description", "exemples", "positif", "negatif"}
    assert sum(detail.values()) == pytest.approx(resultats[0]["score"], abs=0.01)


def test_le_bras_temoin_ne_garde_que_nom_et_description(index):
    resultats = index.chercher("point dettes", sans_declencheurs=True)
    for r in resultats:
        assert set(r["detail"]) <= {"name", "description"}


def test_une_demande_sans_jeton_utile_ne_rend_rien(index):
    assert index.chercher("et le la") == []
    assert index.chercher("") == []


# -- la soustraction du negatif --------------------------------------------


def test_une_contre_indication_fait_reculer_le_skill(tmp_path):
    """Sans la soustraction, « ne pas charger pour une commande client » ferait
    remonter le skill EN TETE sur « commande client », avec les bons mots au
    meilleur endroit possible."""
    store = Store(tmp_path / "n.db")
    peupler(store, {
        "gm-commande-fournisseur": (
            "Commandes fournisseurs.",
            [("Déclencheurs",
              "- « commande fournisseur »\n"
              "- Ne pas charger pour une commande client.")],
        ),
        "gm-commande-client": ("Commandes client.", [("Déclencheurs", "- « commande client »")]),
    })
    index = Index.construire(store)
    try:
        resultats = index.chercher("commande client")
        assert resultats[0]["name"] == "gm-commande-client"
        fournisseur = [r for r in resultats if r["name"] == "gm-commande-fournisseur"]
        if fournisseur:
            assert fournisseur[0]["detail"].get("negatif", 0) < 0
            assert fournisseur[0]["score"] < resultats[0]["score"]
    finally:
        store.close()


# -- le corpus et son exclusion -------------------------------------------


def test_le_corpus_est_etiquete_par_construction(index):
    cas = index.corpus()
    demandes = {d: s for d, s, _ in cas}
    assert demandes["controle des factures fournisseurs"] == "gm-supplier-invoice-control"
    assert demandes["creer une fiche produit"] == "gm-presta-catalogue"
    # Une ligne peut porter plusieurs citations.
    assert "point dettes" in demandes


def test_l_exclusion_evite_la_circularite(index):
    """Sans elle, un skill retrouve trivialement sa propre citation et le
    chiffre du banc ne veut rien dire."""
    ligne = '- « controle des factures fournisseurs »\n- « point dettes »'
    avec = index.chercher("point dettes")
    sans = index.chercher("point dettes", exclure_exemple=ligne.splitlines()[1])
    # La ligne exacte figure dans exemples_brut : on la retrouve pour le test.
    cible = [s for s in index.skills if s.name == "gm-supplier-invoice-control"][0]
    for l in cible.exemples_brut:
        if "point dettes" in l:
            sans = index.chercher("point dettes", exclure_exemple=l)
    score_avec = next(r["score"] for r in avec if r["name"] == cible.name)
    scores_sans = [r["score"] for r in sans if r["name"] == cible.name]
    assert not scores_sans or scores_sans[0] < score_avec


# -- les commandes ---------------------------------------------------------


def test_gk_cherche_rend_le_detail(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / "c.db")
    peupler(store, BIBLIOTHEQUE)
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "c.db"))
    assert cli_main(["--json", "cherche", "point dettes"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["resultats"][0]["name"] == "gm-supplier-invoice-control"
    assert out["bras"] == "tous les champs"
    assert out["resultats"][0]["detail"]


def test_gk_cherche_sur_un_catalogue_vide(tmp_path, capsys, monkeypatch):
    Store(tmp_path / "v.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "v.db"))
    assert cli_main(["--json", "cherche", "quoi que ce soit"]) == 1
    assert "catalogue vide" in json.loads(capsys.readouterr().out)["erreur"]


def test_gk_banc_compare_les_deux_bras(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / "b.db")
    peupler(store, BIBLIOTHEQUE)
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "b.db"))
    assert cli_main(["--json", "banc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cas"] >= 4
    assert "tous_les_champs" in out and "nom_description" in out
    assert "gain_mrr_des_declencheurs" in out
    # La lecture dit d'abord la reserve, avant tout chiffre.
    assert "optimistes" in out["lecture"][0]


def test_gk_banc_sans_corpus(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / "s.db")
    peupler(store, {"a": ("Une description.", [("Déclencheurs", "Sans citation.")])})
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "s.db"))
    assert cli_main(["--json", "banc"]) == 1
    assert "aucune phrase citee" in json.loads(capsys.readouterr().out)["erreur"]


def test_le_banc_distingue_les_deux_sortes_d_echec(tmp_path, capsys, monkeypatch):
    """« aucun resultat » dit que les mots de la phrase n'existent nulle part
    ailleurs dans le skill — une remarque sur la redaction. « mauvais skill »
    dit que le classement se trompe — une remarque sur le score. Les confondre
    ferait regler le score pour un probleme de redaction."""
    store = Store(tmp_path / "e.db")
    peupler(store, {
        "gm-unique": (
            "Description sans rapport.",
            [("Déclencheurs", '- « xyzzy plugh »')],
        ),
        "gm-concurrent": (
            "Factures fournisseurs et dettes.",
            [("Déclencheurs", '- « point dettes »')],
        ),
        "gm-voisin": ("Dettes et factures aussi.", [("Déclencheurs", '- « dettes »')]),
    })
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "e.db"))
    assert cli_main(["--json", "banc"]) == 0
    out = json.loads(capsys.readouterr().out)
    # « xyzzy plugh » n'existe nulle part ailleurs : aucun resultat.
    assert out["echecs_sans_aucun_resultat"] >= 1
    causes = {e["cause"] for e in out["echecs"]}
    assert "aucun resultat" in causes
    assert any("REDACTION" in l for l in out["lecture"])
