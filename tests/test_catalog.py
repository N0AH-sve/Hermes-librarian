"""Tests du catalogue : manifeste, tranches, orphelins.

L'enjeu de ces tests n'est pas la plomberie SQL, c'est le comportement du
manifeste. Trois proprietes doivent tenir sans quoi la phase 2 produirait des
recommandations sur une bibliotheque fausse :

* un manifeste **partiel** ne fait disparaitre personne ;
* une tranche sans intitule correspondant marque tout de meme le skill comme
  indexe, sinon GK la reclame indefiniment ;
* `retire_testcases` refuse d'agir sur un catalogue vide, sans quoi un premier
  demarrage mettrait tout le corpus du banc au rebut.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk.catalog import MAX_SECTION_CHARS, WANTED_MAX, Catalog  # noqa: E402
from gk.corrections import Recorder, retire_testcases  # noqa: E402
from gk.service import Service  # noqa: E402
from gk.store import Store  # noqa: E402


def manifeste(*noms: str, empreinte: str = "1") -> list[dict]:
    return [
        {
            "path": f"cat/{nom}/SKILL.md",
            "name": nom,
            "description": f"description de {nom}",
            "category": "cat",
            "fingerprint": empreinte,
        }
        for nom in noms
    ]


@pytest.fixture()
def cat(tmp_path):
    store = Store(tmp_path / "c.db")
    yield Catalog(store)
    store.close()


# -- le manifeste ----------------------------------------------------------


def test_un_manifeste_cree_et_reclame_les_tranches(cat):
    out = cat.ingest(manifeste("a", "b"), complete=True)
    assert (out["recus"], out["nouveaux"], out["disparus"]) == (2, 2, 0)
    assert sorted(out["wanted"]) == ["cat/a/SKILL.md", "cat/b/SKILL.md"]


def test_un_manifeste_inchange_ne_reclame_rien(cat):
    cat.ingest(manifeste("a"), complete=True)
    cat.store_sections([{"path": "cat/a/SKILL.md", "fingerprint": "1", "sections": []}])
    out = cat.ingest(manifeste("a"), complete=True)
    assert out["modifies"] == 0
    assert out["wanted"] == [], "rien n'a bouge, rien n'est a relire"


def test_une_empreinte_qui_bouge_reclame_de_nouveau(cat):
    cat.ingest(manifeste("a"), complete=True)
    cat.store_sections([{"path": "cat/a/SKILL.md", "fingerprint": "1", "sections": []}])
    out = cat.ingest(manifeste("a", empreinte="2"), complete=True)
    assert out["modifies"] == 1
    assert out["wanted"] == ["cat/a/SKILL.md"]


def test_un_manifeste_complet_marque_les_disparus_sans_les_supprimer(cat):
    cat.ingest(manifeste("a", "b"), complete=True)
    out = cat.ingest(manifeste("a"), complete=True)
    assert out["disparus"] == 1
    assert cat.total() == 1, "b ne compte plus parmi les vivants"
    reste = cat.store.one("SELECT missing_since FROM skills WHERE path = 'cat/b/SKILL.md'")
    assert reste is not None and reste["missing_since"], "garde, date, pas supprime"


def test_un_manifeste_partiel_ne_fait_disparaitre_personne(cat):
    """Sans cette regle, une page manquante viderait le catalogue."""
    cat.ingest(manifeste("a", "b"), complete=True)
    out = cat.ingest(manifeste("a"), complete=False)
    assert out["disparus"] == 0
    assert cat.total() == 2


def test_un_skill_qui_revient_cesse_d_etre_disparu(cat):
    cat.ingest(manifeste("a", "b"), complete=True)
    cat.ingest(manifeste("a"), complete=True)
    cat.ingest(manifeste("a", "b"), complete=True)
    assert cat.total() == 2
    row = cat.store.one("SELECT missing_since FROM skills WHERE path = 'cat/b/SKILL.md'")
    assert row["missing_since"] is None


def test_une_entree_sans_chemin_est_comptee_invalide_et_ignoree(cat):
    out = cat.ingest([{"name": "sans chemin"}, *manifeste("a")], complete=True)
    assert out["invalides"] == 1 and out["recus"] == 1


def test_la_liste_reclamee_est_bornee(cat):
    cat.ingest(manifeste(*[f"s{i}" for i in range(WANTED_MAX + 10)]), complete=True)
    assert len(cat.wanted()) == WANTED_MAX, "le premier manifeste ne lit pas 177 fichiers"


# -- les tranches ----------------------------------------------------------


def test_une_tranche_sans_intitule_marque_quand_meme_comme_indexe(cat):
    """Sinon GK reclame indefiniment les tranches d'un skill qui n'en a aucune."""
    cat.ingest(manifeste("a"), complete=True)
    out = cat.store_sections([{"path": "cat/a/SKILL.md", "fingerprint": "1", "sections": []}])
    assert out["traites"] == 1
    assert out["wanted"] == []


def test_les_tranches_sont_gardees_brutes(cat):
    cat.ingest(manifeste("a"), complete=True)
    cat.store_sections([{
        "path": "cat/a/SKILL.md", "fingerprint": "1",
        "sections": [{"heading": "Déclencheurs", "text": "Charger pour X.\nNe pas pour Y."}],
    }])
    row = cat.store.one("SELECT kind, heading, text FROM skill_sections")
    assert row["kind"] == "brut"
    assert row["heading"] == "Déclencheurs"
    assert "Ne pas pour Y." in row["text"], "le decoupage positif/negatif vient plus tard"


def test_un_fichier_qui_bouge_entre_le_manifeste_et_la_tranche_reste_a_relire(cat):
    cat.ingest(manifeste("a", empreinte="2"), complete=True)
    out = cat.store_sections([{
        "path": "cat/a/SKILL.md", "fingerprint": "1",  # empreinte perimee
        "sections": [{"heading": "Déclencheurs", "text": "x"}],
    }])
    assert out["obsoletes"] == 1
    assert out["wanted"] == ["cat/a/SKILL.md"], "garde la tranche, mais la redemandera"


def test_une_tranche_pour_un_skill_inconnu_est_ignoree(cat):
    out = cat.store_sections([{"path": "cat/fantome/SKILL.md", "sections": []}])
    assert out["inconnus"] == 1 and out["traites"] == 0


def test_une_tranche_trop_longue_est_tronquee(cat):
    cat.ingest(manifeste("a"), complete=True)
    out = cat.store_sections([{
        "path": "cat/a/SKILL.md", "fingerprint": "1",
        "sections": [{"heading": "D", "text": "x" * (MAX_SECTION_CHARS + 500)}],
    }])
    assert out["tronques"] == 1
    row = cat.store.one("SELECT LENGTH(text) n FROM skill_sections")
    assert row["n"] == MAX_SECTION_CHARS


def test_une_nouvelle_tranche_remplace_l_ancienne(cat):
    cat.ingest(manifeste("a"), complete=True)
    for texte in ("premier", "second"):
        cat.store_sections([{
            "path": "cat/a/SKILL.md", "fingerprint": "1",
            "sections": [{"heading": "D", "text": texte}],
        }])
    rows = cat.store.query("SELECT text FROM skill_sections")
    assert [r["text"] for r in rows] == ["second"], "pas d'accumulation"


# -- les cas de test et le catalogue vide ----------------------------------


def test_retire_testcases_refuse_un_catalogue_vide(tmp_path):
    """Le corpus du banc est irremplacable : il ne se reconstruit pas."""
    store = Store(tmp_path / "r.db")
    recorder = Recorder(store)
    store.execute(
        "INSERT INTO corrections (ts, session_id, skill_expected, classification, "
        "patterns_digest) VALUES (1.0, 's', 'un-skill', 'unattributable', 'd')"
    )
    assert retire_testcases(store, set()) == 0, "aucun cas retire sur un catalogue vide"
    assert retire_testcases(store, {"autre-skill"}) == 1, "mais il retire quand il sait"
    store.close()
    assert recorder is not None


def test_live_names_accepte_le_nom_et_le_dossier(cat):
    cat.ingest([{
        "path": "cat/dossier-du-skill/SKILL.md",
        "name": "nom-du-frontmatter",
        "fingerprint": "1",
    }], complete=True)
    noms = cat.live_names()
    assert {"nom-du-frontmatter", "dossier-du-skill"} <= noms


def test_un_skill_disparu_sort_des_noms_vivants(cat):
    cat.ingest(manifeste("a", "b"), complete=True)
    cat.ingest(manifeste("a"), complete=True)
    assert "b" not in cat.live_names()


# -- le canal retour -------------------------------------------------------


def test_le_besoin_annonce_un_catalogue_vide_puis_des_tranches(tmp_path):
    svc = Service.open(tmp_path / "s.db")
    try:
        assert svc.observe("s", "une reponse")["besoins"] == ["catalogue_vide"]
        svc.catalog(manifeste("a"), complete=True)
        assert svc.observe("s", "une reponse")["besoins"] == ["tranches_attendues"]
        svc.sections([{"path": "cat/a/SKILL.md", "fingerprint": "1", "sections": []}])
        assert svc.observe("s", "une reponse")["besoins"] == []
    finally:
        svc.close()


def test_le_service_refuse_une_charge_mal_formee(tmp_path):
    svc = Service.open(tmp_path / "s.db")
    try:
        assert svc.catalog("pas une liste")["ok"] is False  # type: ignore[arg-type]
        assert svc.sections({"path": "x"})["ok"] is False  # type: ignore[arg-type]
    finally:
        svc.close()


def test_health_et_catalogue_disent_la_meme_chose(tmp_path):
    svc = Service.open(tmp_path / "s.db")
    try:
        svc.catalog(manifeste("a", "b"), complete=True)
        sante = svc.health()
        assert sante["ok"] is True
        assert sante["catalogue"]["skills"] == 2
        assert sante["catalogue"]["a_indexer"] == 2
        assert sante["catalogue"]["manifeste_perime"] is False
    finally:
        svc.close()


# -- la commande -----------------------------------------------------------


def test_gk_catalogue_dit_quoi_faire_quand_il_est_vide(tmp_path, capsys, monkeypatch):
    import json

    from gk.cli import main as cli_main

    store = Store(tmp_path / "v.db")
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "v.db"))
    assert cli_main(["--json", "catalogue"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skills"] == 0
    assert "remarque" in out, "un zero doit dire ce qu'il faut faire, pas rester nu"


def test_gk_catalogue_rend_le_detail_quand_il_est_rempli(tmp_path, capsys, monkeypatch):
    import json

    from gk.cli import main as cli_main

    store = Store(tmp_path / "p.db")
    Catalog(store).ingest(manifeste("a", "b"), complete=True)
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "p.db"))
    assert cli_main(["--json", "catalogue"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skills"] == 2 and out["a_indexer"] == 2
    assert out["par_categorie"] == {"cat": 2}
    assert sorted(out["a_reclamer_au_prochain_tour"]) == [
        "cat/a/SKILL.md", "cat/b/SKILL.md"
    ]


def test_gk_check_se_plaint_d_un_catalogue_vide(tmp_path, capsys, monkeypatch):
    import json

    from gk.cli import main as cli_main

    store = Store(tmp_path / "k.db")
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "k.db"))
    cli_main(["--json", "check"])
    out = json.loads(capsys.readouterr().out)
    assert any("catalogue vide" in p for p in out["problemes"])
    # Et plus de plainte sur une racine de skills illisible : GK ne lit plus le
    # disque de l'agent, donc une racine absente est l'etat normal.
    assert "racine_skills" not in out


# -- deux sections, un seul titre -----------------------------------------


def test_deux_sections_du_meme_titre_ne_s_ecrasent_pas(cat):
    """GM ecrit 82 sections intitulees `## Declencheur :`. La clef etant
    (path, kind, heading), la seconde ecrasait la premiere EN SILENCE -- et
    aucun comptage ne permettait de s'en apercevoir, puisque les doublons
    disparaissaient avant d'etre comptes."""
    cat.ingest(manifeste("a"), complete=True)
    out = cat.store_sections([{
        "path": "cat/a/SKILL.md", "fingerprint": "1",
        "sections": [
            {"heading": "Déclencheur :", "text": "« commande fournisseur »"},
            {"heading": "Déclencheur :", "text": "« point dettes »"},
            {"heading": "Déclencheur :", "text": "« reappro »"},
        ],
    }])
    assert out["sections_ecrites"] == 3
    assert out["titres_repetes"] == 2
    rows = cat.store.query("SELECT kind, text FROM skill_sections ORDER BY kind")
    assert len(rows) == 3, "les trois sont gardees"
    assert [r["kind"] for r in rows] == ["brut", "brut#2", "brut#3"]
    textes = " ".join(r["text"] for r in rows)
    for attendu in ("commande fournisseur", "point dettes", "reappro"):
        assert attendu in textes
    # Et le titre reste fidele : c'est `kind` qui porte le rang.
    assert {r["heading"] for r in cat.store.query("SELECT heading FROM skill_sections")} == {
        "Déclencheur :"
    }


def test_les_titres_repetes_se_comptent_dans_l_etat(cat):
    cat.ingest(manifeste("a"), complete=True)
    cat.store_sections([{
        "path": "cat/a/SKILL.md", "fingerprint": "1",
        "sections": [
            {"heading": "Déclencheur :", "text": "un"},
            {"heading": "Déclencheur :", "text": "deux"},
        ],
    }])
    assert cat.stats()["sections_a_titre_repete"] == 1


def test_une_nouvelle_livraison_remplace_les_rangs(cat):
    """Le DELETE par chemin precede l'insertion : pas d'accumulation de rangs."""
    cat.ingest(manifeste("a"), complete=True)
    for n in (3, 1):
        cat.store_sections([{
            "path": "cat/a/SKILL.md", "fingerprint": "1",
            "sections": [{"heading": "D :", "text": str(i)} for i in range(n)],
        }])
    rows = cat.store.query("SELECT kind FROM skill_sections")
    assert [r["kind"] for r in rows] == ["brut"], "une seule section reste"


# -- tout relire apres un changement de regle -----------------------------


def test_reindexer_fait_tout_redemander(cat):
    """Les empreintes des fichiers ne bougent pas quand c'est NOTRE regle de
    lecture qui change. Sans cette commande, un correctif de decoupage ne
    s'appliquerait qu'aux skills edites ensuite."""
    cat.ingest(manifeste("a", "b"), complete=True)
    for nom in ("a", "b"):
        cat.store_sections([
            {"path": f"cat/{nom}/SKILL.md", "fingerprint": "1", "sections": []}
        ])
    assert cat.wanted() == [], "tout est indexe"
    assert cat.reindexer() == 2
    assert sorted(cat.wanted()) == ["cat/a/SKILL.md", "cat/b/SKILL.md"]
    # Et il reclame un manifeste, sinon il faudrait attendre la ronde horaire.
    assert cat.manifeste_demande() is True


def test_reindexer_ne_touche_pas_les_disparus(cat):
    cat.ingest(manifeste("a", "b"), complete=True)
    cat.ingest(manifeste("a"), complete=True)
    assert cat.reindexer() == 1, "seuls les vivants"


def test_reindexer_ne_touche_ni_les_corrections_ni_les_compteurs(cat):
    cat.store.execute(
        "INSERT INTO corrections (ts, session_id, skill_expected, classification, "
        "patterns_digest) VALUES (1.0, 's', 'un-skill', 'unattributable', 'd')"
    )
    cat.store.bump("observe_calls", by=7)
    cat.ingest(manifeste("a"), complete=True)
    cat.reindexer()
    assert cat.store.one("SELECT COUNT(*) c FROM corrections")["c"] == 1
    assert cat.store.counter("observe_calls") == 7


def test_la_commande_reindexer_dit_combien_de_skills(tmp_path, capsys, monkeypatch):
    import json as _json

    from gk.cli import main as cli_main

    s = Store(tmp_path / "ri.db")
    Catalog(s).ingest(manifeste("a", "b", "c"), complete=True)
    s.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "ri.db"))
    assert cli_main(["--json", "reindexer"]) == 0
    assert _json.loads(capsys.readouterr().out)["skills_a_relire"] == 3
