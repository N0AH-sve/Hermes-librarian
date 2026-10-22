"""Tests du tri des declencheurs.

Ce module decide si la colonne a poids negatif merite d'exister. Un comptage
faux ferait abandonner une bonne idee, ou construire une mauvaise -- dans les
deux cas sur un chiffre qui serait ensuite recopie dans le document qui fait
foi. D'ou des tests sur le comptage, pas seulement sur le decoupage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk.catalog import Catalog  # noqa: E402
from gk.cli import main as cli_main  # noqa: E402
from gk.declencheurs import decouper, lecture, marqueurs_de, mesurer, titre_negatif  # noqa: E402
from gk.store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "d.db")
    yield s
    s.close()


def peupler(store: Store, skills: dict[str, list[tuple[str, str]]]) -> None:
    """`{nom: [(titre, texte), ...]}` -> catalogue + sections."""
    cat = Catalog(store)
    cat.ingest(
        [
            {"path": f"cat/{nom}/SKILL.md", "name": nom, "fingerprint": "1"}
            for nom in skills
        ],
        complete=True,
    )
    cat.store_sections([
        {
            "path": f"cat/{nom}/SKILL.md",
            "fingerprint": "1",
            "sections": [{"heading": h, "text": t} for h, t in sections],
        }
        for nom, sections in skills.items()
    ])


# -- le decoupage ----------------------------------------------------------


def test_un_titre_negatif_envoie_tout_du_cote_negatif():
    positif, negatif, _ = decouper("Ne pas charger si", "La demande porte sur un avoir.")
    assert positif == ""
    assert "avoir" in negatif


def test_le_decoupage_se_fait_ligne_par_ligne():
    texte = (
        "Charger pour une commande fournisseur.\n"
        "Ne pas charger pour une commande client.\n"
        "Charger aussi pour un reappro.\n"
    )
    positif, negatif, vus = decouper("Déclencheurs", texte)
    assert "commande fournisseur" in positif and "reappro" in positif
    assert "commande client" in negatif
    assert "commande client" not in positif, "sinon le skill remonte sur son voisin"
    assert vus["ne pas"] == 1


def test_les_accents_et_la_casse_ne_changent_rien():
    assert marqueurs_de("NE PAS charger") == marqueurs_de("ne pas charger")
    assert titre_negatif("NE PAS CHARGER SI") is True
    assert titre_negatif("Déclencheurs") is False


def test_une_section_sans_negation_reste_entierement_positive():
    positif, negatif, vus = decouper("Déclencheurs", "Charger quand X.\nEt quand Y.\n")
    assert negatif == "" and vus == {} and "Y" in positif


def test_les_formes_anglaises_sont_reperees():
    _, negatif, vus = decouper("When to use", "Load for X.\nDo not load for Y.\n")
    assert "Y" in negatif and "do not" in vus


# -- le comptage, qui porte la decision ------------------------------------


def test_structurel_et_prose_ne_se_comptent_pas_deux_fois(store):
    peupler(store, {
        "skill-structurel": [
            ("Déclencheurs", "Charger pour X."),
            ("Ne pas charger si", "La demande porte sur Y."),
        ],
        "skill-en-prose": [
            ("Déclencheurs", "Charger pour X.\nNe pas charger pour Y."),
        ],
        "skill-sans": [("Déclencheurs", "Charger pour X.")],
    })
    m = mesurer(store)
    assert m["skills_avec_declencheurs"] == 3
    assert m["negatif_structurel"] == 1
    assert m["negatif_en_prose"] == 1, "le structurel n'est pas recompte en prose"
    assert m["sans_contre_indication"] == 1
    # Les trois categories couvrent exactement la population.
    assert (
        m["negatif_structurel"] + m["negatif_en_prose"] + m["sans_contre_indication"]
        == m["skills_avec_declencheurs"]
    )


def test_chaque_marqueur_est_compte_a_part(store):
    """Un marqueur bruyant doit se voir pour pouvoir etre retire. `jamais` seul
    est le plus suspect : « n'a jamais commande » est une condition positive."""
    peupler(store, {
        "a": [("Déclencheurs", "Le client n'a jamais commande.")],
        "b": [("Déclencheurs", "Ne pas charger pour un avoir.")],
    })
    m = mesurer(store)
    assert m["par_marqueur"]["jamais"] == 1
    assert m["par_marqueur"]["ne pas"] == 1


def test_les_citations_d_autres_skills_sont_comptees(store):
    """Le risque de precision de l'ajout des declencheurs : un skill qui cite
    son voisin remonte sur les demandes du voisin."""
    peupler(store, {
        "gm-presta-commande": [
            ("Déclencheurs", "Commande fournisseur. Sinon voir gm-presta-catalogue."),
        ],
        "gm-presta-catalogue": [("Déclencheurs", "Fiches produits.")],
    })
    m = mesurer(store)
    assert m["skills_citant_un_autre_skill"] == 1
    cite = next(iter(m["citations"].values()))
    assert cite == ["gm-presta-catalogue"]


def test_un_skill_ne_se_cite_pas_lui_meme(store):
    peupler(store, {
        "gm-presta-commande": [("Déclencheurs", "Voir gm-presta-commande pour le reste.")],
    })
    assert mesurer(store)["skills_citant_un_autre_skill"] == 0


def test_le_poids_se_repartit_entre_positif_et_negatif(store):
    peupler(store, {
        "a": [("Déclencheurs", "Charger pour X.\nNe pas charger pour Y.")],
    })
    m = mesurer(store)
    assert m["octets_positifs"] > 0 and m["octets_negatifs"] > 0


def test_les_exemples_sont_bornes_et_desactives_par_defaut(store):
    peupler(store, {"a": [("Déclencheurs", "x\nNe pas charger pour " + "y" * 300)]})
    assert mesurer(store)["exemples"] == []
    exemples = mesurer(store, exemples=True)["exemples"]
    assert exemples and all(len(e) <= 120 for e in exemples)


# -- la lecture ------------------------------------------------------------


def test_la_lecture_conclut_a_l_abandon_quand_c_est_marginal():
    m = {
        "skills_au_catalogue": 182, "skills_avec_declencheurs": 65,
        "negatif_structurel": 0, "negatif_en_prose": 5,
        "skills_citant_un_autre_skill": 0,
    }
    texte = " ".join(lecture(m))
    assert "couterait plus qu'elle ne rapporte" in texte


def test_la_lecture_conclut_qu_elle_vaut_son_code_quand_c_est_net():
    m = {
        "skills_au_catalogue": 182, "skills_avec_declencheurs": 65,
        "negatif_structurel": 20, "negatif_en_prose": 40,
        "skills_citant_un_autre_skill": 10,
    }
    texte = " ".join(lecture(m))
    assert "vaut son code" in texte
    assert "risque de precision" in texte


def test_la_lecture_reclame_un_titre_dedie_si_rien_n_est_structurel():
    m = {
        "skills_au_catalogue": 100, "skills_avec_declencheurs": 50,
        "negatif_structurel": 0, "negatif_en_prose": 30,
        "skills_citant_un_autre_skill": 0,
    }
    assert any("gm-skill-template" in l for l in lecture(m))


# -- la commande -----------------------------------------------------------


def test_gk_declencheurs_dit_quoi_faire_quand_il_n_a_rien(tmp_path, capsys, monkeypatch):
    Store(tmp_path / "v.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "v.db"))
    assert cli_main(["--json", "declencheurs"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skills_avec_declencheurs"] == 0
    assert "remarque" in out and "lecture" not in out


def test_gk_declencheurs_rend_la_lecture_quand_il_a_des_sections(tmp_path, capsys, monkeypatch):
    s = Store(tmp_path / "p.db")
    peupler(s, {"a": [("Déclencheurs", "Charger pour X.\nNe pas charger pour Y.")]})
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "p.db"))
    assert cli_main(["--json", "declencheurs"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["negatif_en_prose"] == 1
    assert out["lecture"], "un chiffre doit arriver avec sa lecture"
