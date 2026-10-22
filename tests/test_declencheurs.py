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


def test_un_renvoi_explicite_et_une_mention_ne_se_confondent_pas(store):
    """Le premier releve comptait 16 skills « citant un autre skill », mais
    `pennylane` et `prestashop` sont des OUTILS qui ont un skill portant leur
    nom. Les mentionner, c'est nommer son domaine ; renvoyer vers un voisin,
    c'est le risque de precision. Les confondre le surestime."""
    peupler(store, {
        "gm-renvoi": [
            ("Déclencheurs", "Commande fournisseur. Sinon voir gm-catalogue."),
        ],
        "gm-mention": [
            ("Déclencheurs", "Croiser les factures Qonto et pennylane."),
        ],
        "gm-catalogue": [("Déclencheurs", "Fiches produits.")],
        "pennylane": [("Déclencheurs", "Comptabilite.")],
    })
    m = mesurer(store)
    assert m["skills_mentionnant_un_autre_skill"] == 2, "les deux mentionnent"
    assert m["skills_avec_renvoi_explicite"] == 1, "un seul renvoie"
    assert next(iter(m["renvois"].values())) == ["gm-catalogue"]


def test_une_citation_du_cote_negatif_compte_comme_un_renvoi(store):
    """Un renvoi ecrit dans la moitie negative est le bon usage : il reste utile
    a l'humain et cesse de polluer l'index positif."""
    peupler(store, {
        "gm-commande": [
            ("Déclencheurs", "Commande fournisseur.\nNe pas charger pour les fiches : gm-catalogue."),
        ],
        "gm-catalogue": [("Déclencheurs", "Fiches produits.")],
    })
    m = mesurer(store)
    assert m["skills_avec_renvoi_explicite"] == 1


def test_un_skill_ne_se_cite_pas_lui_meme(store):
    peupler(store, {
        "gm-presta-commande": [("Déclencheurs", "Voir gm-presta-commande pour le reste.")],
    })
    assert mesurer(store)["skills_mentionnant_un_autre_skill"] == 0


def test_les_exemples_entre_guillemets_sont_comptes(store):
    """Releve du 2026-10-22 : les declencheurs GM sont redigees comme des
    citations de l'utilisateur. Donc du texte de la meme forme que les
    demandes, ce qui en fait le signal le plus fort possible."""
    peupler(store, {
        "gm-factures": [(
            "Quand charger ce skill",
            "- \u00ab point dettes \u00bb, \u00ab point compta \u00bb\n"
            "- \u00ab ou en sont les factures fournisseurs \u00bb\n"
            "- Audit d'ecart entre Presta et Qonto",
        )],
        "gm-sans-exemples": [("Déclencheurs", "Charger pour les audits.")],
    })
    m = mesurer(store)
    assert m["skills_avec_exemples_cites"] == 1
    assert m["lignes_entre_guillemets"] == 2
    assert any("poids le plus eleve" in l for l in lecture(m))


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
        "skills_mentionnant_un_autre_skill": 0, "skills_avec_renvoi_explicite": 0,
    }
    texte = " ".join(lecture(m))
    assert "couterait plus qu'elle ne rapporte" in texte


def test_la_lecture_conclut_qu_elle_vaut_son_code_quand_c_est_net():
    m = {
        "skills_au_catalogue": 182, "skills_avec_declencheurs": 65,
        "negatif_structurel": 20, "negatif_en_prose": 40,
        "skills_mentionnant_un_autre_skill": 12, "skills_avec_renvoi_explicite": 10,
    }
    texte = " ".join(lecture(m))
    assert "vaut son code" in texte
    assert "risque de precision" in texte


def test_la_lecture_reclame_un_titre_dedie_si_rien_n_est_structurel():
    m = {
        "skills_au_catalogue": 100, "skills_avec_declencheurs": 50,
        "negatif_structurel": 0, "negatif_en_prose": 30,
        "skills_mentionnant_un_autre_skill": 0, "skills_avec_renvoi_explicite": 0,
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


# -- le chevauchement des marqueurs, et la marge d'erreur ------------------


def test_un_marqueur_n_est_pas_compte_deux_fois():
    """Le defaut du premier relevé : une ligne « ne jamais » comptait pour
    `ne jamais` ET pour `jamais`, d'ou un `jamais: 35` gonfle par son propre
    sous-marqueur."""
    assert marqueurs_de("Ne jamais charger ceci") == ["ne jamais"]
    assert marqueurs_de("Le client n'a jamais commande") == ["jamais"]
    assert marqueurs_de("Ne pas charger") == ["ne pas"]


def test_le_comptage_strict_ecarte_les_marqueurs_faibles(store):
    """« le client n'a jamais commande » est une condition positive, pas une
    contre-indication. La decision se prend sur le comptage strict."""
    peupler(store, {
        "faible": [("Déclencheurs", "Charger si le client n'a jamais commande.")],
        "fort": [("Déclencheurs", "Charger pour X.\nNe pas charger pour Y.")],
    })
    m = mesurer(store)
    assert m["negatif_en_prose"] == 2, "comptage large : les deux"
    assert m["negatif_en_prose_strict"] == 1, "comptage strict : le vrai seul"


def test_la_lecture_annonce_la_marge_d_erreur():
    m = {
        "skills_au_catalogue": 182, "skills_avec_declencheurs": 65,
        "negatif_structurel": 3, "negatif_en_prose": 21,
        "negatif_en_prose_strict": 9,
        "skills_mentionnant_un_autre_skill": 16, "skills_avec_renvoi_explicite": 6,
    }
    texte = " ".join(lecture(m))
    assert "12 skills" in texte, "la decision se prend sur le strict : 3 + 9"
    assert "Au plus 24" in texte, "et la borne large est dite"


# -- gk skill et gk rafraichir --------------------------------------------


def test_gk_skill_montre_le_decoupage(tmp_path, capsys, monkeypatch):
    s = Store(tmp_path / "s.db")
    peupler(s, {"gm-presta-commande": [
        ("Déclencheurs", "Charger pour une commande fournisseur.\nNe pas charger pour un avoir."),
    ]})
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "s.db"))
    assert cli_main(["--json", "skill", "gm-presta-commande"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["a_jour"] is True
    rubrique = out["declencheurs"][0]
    assert "commande fournisseur" in rubrique["positif"]
    assert "avoir" in rubrique["negatif"]
    assert "ne pas" in rubrique["marqueurs"]


def test_gk_skill_dit_franchement_quand_il_n_y_a_rien(tmp_path, capsys, monkeypatch):
    s = Store(tmp_path / "s.db")
    peupler(s, {"gm-sans": []})
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "s.db"))
    assert cli_main(["--json", "skill", "gm-sans"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["declencheurs"] == []
    assert "que le nom" in out["remarque"]


def test_gk_skill_sur_un_nom_inconnu(tmp_path, capsys, monkeypatch):
    Store(tmp_path / "s.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "s.db"))
    assert cli_main(["--json", "skill", "fantome"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_rafraichir_fait_reclamer_un_manifeste_au_prochain_message(tmp_path, capsys, monkeypatch):
    """GK ne parle jamais le premier : il pose un drapeau que le prochain
    `observe` traduit en besoin, et le pont l'honore sans modification."""
    from gk.service import Service

    svc = Service.open(tmp_path / "r.db")
    try:
        svc.catalog(
            [{"path": "cat/a/SKILL.md", "name": "a", "fingerprint": "1"}], complete=True
        )
        svc.sections([{"path": "cat/a/SKILL.md", "fingerprint": "1", "sections": []}])
        assert svc.observe("s", "une reponse")["besoins"] == [], "rien a reclamer"
        svc.catalog_.demander_manifeste()
        assert svc.observe("s", "une reponse")["besoins"] == ["tranches_attendues"]
        # Le manifeste arrive : la demande est servie, et le besoin s'eteint.
        svc.catalog(
            [{"path": "cat/a/SKILL.md", "name": "a", "fingerprint": "1"}], complete=True
        )
        assert svc.observe("s", "une reponse")["besoins"] == []
    finally:
        svc.close()


def test_la_commande_rafraichir_pose_le_drapeau(tmp_path, capsys, monkeypatch):
    s = Store(tmp_path / "c.db")
    peupler(s, {"a": []})
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "c.db"))
    assert cli_main(["--json", "rafraichir"]) == 0
    assert json.loads(capsys.readouterr().out)["demande"] is True
    s = Store(tmp_path / "c.db")
    assert Catalog(s).manifeste_demande() is True
    s.close()


def test_gk_skill_propose_des_candidats_sur_une_faute(tmp_path, capsys, monkeypatch):
    """Les noms reels sont longs et prefixes ; une faute de frappe ne doit pas
    couter la lecture d'un `gk catalogue` complet.

    Le decoupage se fait sur les tirets et non sur les espaces : un mot utile
    d'un nom comme `gm-supplier-invoice-control` est separe par un tiret.
    """
    s = Store(tmp_path / "s.db")
    peupler(s, {
        "gm-supplier-invoice-control": [],
        "gm-supplier-orders": [],
        "creative-claude-design": [],
    })
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "s.db"))

    # Un fragment suffit, sans passer par les candidats.
    assert cli_main(["--json", "skill", "invoice"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "gm-supplier-invoice-control"

    # Une faute : un mot du nom reste reconnaissable.
    assert cli_main(["--json", "skill", "gm-supplier-invoic-controle"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["candidats"] == ["gm-supplier-invoice-control", "gm-supplier-orders"]
    assert "creative-claude-design" not in out["candidats"]


def test_gk_skill_renvoie_au_catalogue_quand_rien_ne_ressemble(tmp_path, capsys, monkeypatch):
    """Et il ne faut pas inventer une ressemblance : `gm-presta-commande`, nom
    employe dans les exemples du document d'architecture, n'existe pas dans la
    bibliotheque reelle. La bonne reponse est de le dire."""
    s = Store(tmp_path / "s.db")
    peupler(s, {"gm-supplier-invoice-control": [], "gm-amazon-seller-central": []})
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "s.db"))
    assert cli_main(["--json", "skill", "gm-presta-commande"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["candidats"] == []
    assert "gk catalogue" in out["remarque"]


# -- le titre porte parfois la phrase -------------------------------------


def test_une_phrase_dans_le_titre_est_comptee_comme_un_exemple(store):
    """GM ecrit `## Declencheur: "commande fournisseur"`. Ne regarder que le
    corps perdait tout ce travail, et le perdait en silence puisque la section
    etait bien stockee."""
    peupler(store, {
        "gm-titre": [('Déclencheur: « commande fournisseur »', "Details en prose.")],
        "gm-corps": [("Déclencheurs", "- « point dettes »")],
    })
    m = mesurer(store)
    assert m["skills_avec_exemples_cites"] == 2
    assert m["lignes_entre_guillemets"] == 2
