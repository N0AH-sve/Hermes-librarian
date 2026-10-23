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


# -- les deux populations, et les demandes peu informatives ----------------


def test_le_banc_separe_le_metier_du_generique(tmp_path, capsys, monkeypatch):
    """Les cinq premiers echecs du relevé du 2026-10-22 etaient TOUS des skills
    generiques. Une moyenne des deux populations ne decrit ni l'une ni l'autre."""
    store = Store(tmp_path / "p.db")
    cat = Catalog(store)
    cat.ingest([
        {"path": "greenmusic/gm-factures/SKILL.md", "name": "gm-factures",
         "description": "Factures fournisseurs et dettes.",
         "category": "greenmusic", "fingerprint": "1"},
        {"path": "software-development/plan/SKILL.md", "name": "plan",
         "description": "Planifier un travail.",
         "category": "software-development", "fingerprint": "1"},
    ], complete=True)
    cat.store_sections([
        {"path": "greenmusic/gm-factures/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs",
                       "text": '- « point dettes fournisseurs »'}]},
        # Deux jetons au moins, sinon `corpus()` ecarte la phrase : une
        # citation d'un seul mot ne fait pas un cas de test.
        {"path": "software-development/plan/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs", "text": '- « planifier le travail »'}]},
    ])
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "p.db"))
    assert cli_main(["--json", "banc"]) == 0
    out = json.loads(capsys.readouterr().out)
    pop = out["par_population"]
    assert "metier" in pop and "generique" in pop
    assert pop["metier"]["cas"] + pop["generique"]["cas"] == out["cas"]
    # Chaque population porte aussi son bras temoin.
    assert "mrr_nom_description" in pop["metier"]


def test_les_demandes_peu_informatives_sont_comptees_a_part(tmp_path, capsys, monkeypatch):
    """« etape 1 validee, go » ne porte aucun mot de domaine : ce sont des
    declencheurs d'etat de conversation, qu'aucune recherche par les mots ne
    peut servir. Les noyer dans la moyenne serait trompeur ; les ecarter du
    jugement serait malhonnete. On les compte a part."""
    store = Store(tmp_path / "i.db")
    cat = Catalog(store)
    skills = {
        f"s{i}": f"Description du domaine numero {i} avec des mots propres{i}."
        for i in range(12)
    }
    cat.ingest([
        {"path": f"cat/{n}/SKILL.md", "name": n, "description": d,
         "category": "greenmusic", "fingerprint": "1"}
        for n, d in skills.items()
    ], complete=True)
    cat.store_sections([
        {"path": f"cat/{n}/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs",
                       "text": f'- « propres{i} du domaine »' if i else '- « go et voila »'}]}
        for i, n in enumerate(skills)
    ])
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "i.db"))
    assert cli_main(["--json", "banc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "peu_informative" in out["par_population"]
    assert "informative" in out["par_population"]
    assert out["seuil_information"] > 0


def test_l_information_d_une_demande_se_mesure(tmp_path):
    store = Store(tmp_path / "x.db")
    peupler(store, BIBLIOTHEQUE)
    index = Index.construire(store)
    try:
        # Un mot rare porte plus qu'une tournure sans domaine.
        assert index.information("proforma") > index.information("et le la")
        assert index.information("et le la") == 0.0, "que des mots vides"
    finally:
        store.close()


# -- l'inversion selon la population --------------------------------------


def test_la_categorie_ne_change_jamais_le_score(tmp_path):
    """Invariant ne d'une regle mesuree puis RETIREE.

    Un bras du banc ponderait par population : les declencheurs indexes pour le
    metier seulement. Resultat : les scores devenaient incomparables, un skill
    note sur quatre champs battant mecaniquement un skill note sur deux. La
    regle avantageait donc le metier au lieu d'ameliorer la recherche, et son
    gain de +0,008 etait un artefact.

    Tous les skills sont notes sur les memes champs avec les memes poids, et ce
    test le verifie plutot que de le laisser a la discipline.
    """
    store = Store(tmp_path / "eq.db")
    cat = Catalog(store)
    for categorie, nom in (("greenmusic", "gm-un"), ("creative", "autre-un")):
        cat.ingest([{
            "path": f"{categorie}/{nom}/SKILL.md", "name": nom,
            "description": "Zorglub et ses particularites.",
            "category": categorie, "fingerprint": "1",
        }], complete=False)
        cat.store_sections([{
            "path": f"{categorie}/{nom}/SKILL.md", "fingerprint": "1",
            "sections": [{"heading": "Déclencheurs", "text": "- « zorglub partout »"}],
        }])
    index = Index.construire(store)
    try:
        resultats = {r["name"]: r["score"] for r in index.chercher("zorglub")}
        assert len(resultats) == 2
        assert resultats["gm-un"] == resultats["autre-un"], (
            "un texte identique doit donner un score identique, quelle que soit "
            "la categorie"
        )
    finally:
        store.close()


def test_le_bras_citations_jette_la_prose(tmp_path):
    """Les citations sont des phrases d'utilisateur, propres ; la prose est
    verbeuse et c'est la que vivent les renvois vers les voisins."""
    store = Store(tmp_path / "pr.db")
    cat = Catalog(store)
    cat.ingest([{
        "path": "greenmusic/gm-a/SKILL.md", "name": "gm-a",
        "description": "Un skill.", "category": "greenmusic", "fingerprint": "1",
    }], complete=True)
    cat.store_sections([{
        "path": "greenmusic/gm-a/SKILL.md", "fingerprint": "1",
        "sections": [{"heading": "Déclencheurs",
                      "text": "- « citation propre »\nProse avec un renvoi zorglub."}],
    }])
    index = Index.construire(store)
    try:
        assert index.chercher("zorglub"), "la prose est indexee par defaut"
        assert index.chercher("zorglub", sans_prose=True) == [], "et jetee sur demande"
        assert index.chercher("citation propre", sans_prose=True), "la citation reste"
    finally:
        store.close()


def test_le_banc_rend_le_bras_citations(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / "bc.db")
    cat = Catalog(store)
    cat.ingest([
        {"path": "greenmusic/gm-a/SKILL.md", "name": "gm-a",
         "description": "Factures proforma.", "category": "greenmusic",
         "fingerprint": "1"},
        {"path": "creative/comic/SKILL.md", "name": "comic",
         "description": "Bandes dessinees.", "category": "creative",
         "fingerprint": "1"},
    ], complete=True)
    cat.store_sections([
        {"path": "greenmusic/gm-a/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs", "text": "- « proforma fournisseur »"}]},
        {"path": "creative/comic/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs", "text": "- « planche dessinee »"}]},
    ])
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "bc.db"))
    assert cli_main(["--json", "banc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "citations_sans_prose" in out
    assert "declencheurs_metier_seulement" not in out, "le bras invalide est retire"
    for bloc in out["par_population"].values():
        assert "mrr_citations_sans_prose" in bloc


def test_la_lecture_ne_pretend_plus_les_demandes_introuvables(tmp_path, capsys, monkeypatch):
    """Une premiere version affirmait qu'aucune recherche par les mots ne
    pouvait servir les demandes sans domaine. Les donnees la contredisaient :
    0,577 de rappel@5. L'affirmation est corrigee dans le texte imprime."""
    store = Store(tmp_path / "l.db")
    cat = Catalog(store)
    noms = {f"s{i}": f"Domaine propre{i} et ses particularites." for i in range(12)}
    cat.ingest([
        {"path": f"greenmusic/{n}/SKILL.md", "name": n, "description": d,
         "category": "greenmusic", "fingerprint": "1"}
        for n, d in noms.items()
    ], complete=True)
    cat.store_sections([
        {"path": f"greenmusic/{n}/SKILL.md", "fingerprint": "1",
         "sections": [{"heading": "Déclencheurs",
                       "text": f'- « propre{i} du domaine »' if i else '- « bon alors voila »'}]}
        for i, n in enumerate(noms)
    ])
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "l.db"))
    assert cli_main(["--json", "banc"]) == 0
    texte = " ".join(json.loads(capsys.readouterr().out)["lecture"])
    assert "Aucune recherche par les mots ne peut" not in texte
    assert "plus dures, pas introuvables" in texte


# -- la normalisation par la longueur -------------------------------------


def test_un_champ_long_perd_son_avantage_de_hasard(tmp_path):
    """Relevé du 2026-10-22 : pour les skills generiques, nom+description seuls
    battent toute combinaison essayee. Ni la prose ni les declencheurs n'en sont
    la cause -- c'est le fait d'AJOUTER DU TEXTE. Un champ qui porte plus de
    jetons distincts rencontre plus de demandes par hasard."""
    store = Store(tmp_path / "ln.db")
    cat = Catalog(store)
    verbeux = " ".join(f"mot{i}" for i in range(200))
    cat.ingest([
        {"path": "cat/precis/SKILL.md", "name": "precis",
         "description": "zorglub.", "category": "cat", "fingerprint": "1"},
        {"path": "cat/verbeux/SKILL.md", "name": "verbeux",
         "description": f"zorglub {verbeux}", "category": "cat", "fingerprint": "1"},
    ], complete=True)
    cat.store_sections([
        {"path": "cat/precis/SKILL.md", "fingerprint": "1", "sections": []},
        {"path": "cat/verbeux/SKILL.md", "fingerprint": "1", "sections": []},
    ])
    index = Index.construire(store)
    try:
        # Sans normalisation, les deux ont le meme score sur « zorglub » : la
        # presence ne distingue pas un champ court d'un champ long.
        brut = {r["name"]: r["score"] for r in index.chercher("zorglub")}
        assert brut["precis"] == brut["verbeux"]
        # Avec, le champ court est avantage, ce qui est le but : il a moins de
        # chances d'avoir rencontre le mot par hasard.
        norme = {
            r["name"]: r["score"]
            for r in index.chercher("zorglub", normaliser_longueur=True)
        }
        assert norme["precis"] > norme["verbeux"]
    finally:
        store.close()


def test_le_facteur_de_longueur_vaut_un_a_la_moyenne(tmp_path):
    """Rapportee a la moyenne du champ, pour que les poids gardent leur sens."""
    store = Store(tmp_path / "fl.db")
    peupler(store, BIBLIOTHEQUE)
    index = Index.construire(store)
    try:
        moyenne = index.longueur_moyenne["description"]
        assert moyenne > 0
        assert index.facteur_longueur("description", round(moyenne)) == pytest.approx(
            1.0, abs=0.05
        )
        assert index.facteur_longueur("description", 1) > 1.0
        assert index.facteur_longueur("description", 500) < 1.0
        # Un champ vide ou un champ inconnu ne change rien.
        assert index.facteur_longueur("description", 0) == 1.0
        assert index.facteur_longueur("inexistant", 10) == 1.0
    finally:
        store.close()


def test_le_banc_rend_le_bras_normalise(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / "bn.db")
    peupler(store, BIBLIOTHEQUE)
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "bn.db"))
    assert cli_main(["--json", "banc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "normalise_par_longueur" in out
    for bloc in out["par_population"].values():
        assert "mrr_normalise" in bloc


# -- la decomposition des echecs ------------------------------------------


def test_le_diagnostic_distingue_redaction_et_score(tmp_path):
    """« 88 echecs » est un tas indifferencie, et les remedes sont opposes : la
    redaction d'un cote, le score de l'autre. Ce decoupage borne la valeur du
    reglage des coefficients."""
    store = Store(tmp_path / "dg.db")
    peupler(store, {
        "gm-absent": ("Rien a voir avec la demande.", []),
        "gm-domine": ("proforma.", []),
        "gm-dominant": ("proforma fournisseur facture port.", []),
        "gm-ordre": ("zorglub particulier.", []),
        "gm-concurrent": ("zorglub autre chose.", []),
    })
    index = Index.construire(store)
    try:
        # Aucun jeton porte : c'est de la redaction.
        d = index.diagnostic("proforma fournisseur", "gm-absent")
        assert d["cause"] == "texte_absent" and d["remede"] == "redaction"
        assert d["jetons_portes"] == 0

        # Le gagnant porte tout ce que porte l'attendu, et plus : redaction
        # aussi, aucun coefficient ne peut inverser cela.
        d = index.diagnostic("proforma fournisseur facture", "gm-domine", limite=1)
        assert d["cause"] == "texte_domine" and d["remede"] == "redaction"
        assert d["gagnant"] == "gm-dominant"
        assert d["jetons_du_gagnant"] > d["jetons_portes"]
    finally:
        store.close()


def test_le_diagnostic_ne_dit_rien_quand_le_cas_reussit(tmp_path):
    store = Store(tmp_path / "ok.db")
    peupler(store, BIBLIOTHEQUE)
    index = Index.construire(store)
    try:
        d = index.diagnostic("proforma", "gm-supplier-invoice-control")
        assert d["cause"] is None and d["rang"] == 1
    finally:
        store.close()


def test_le_banc_decompose_les_echecs_par_remede(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / "db.db")
    peupler(store, BIBLIOTHEQUE)
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "db.db"))
    assert cli_main(["--json", "banc"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "echecs_par_cause" in out
    assert set(out["echecs_par_remede"]) == {"redaction", "score"}
    assert out["echecs_par_remede"]["redaction"] + out["echecs_par_remede"]["score"] \
        <= out["echecs_mauvais_skill"] + out["echecs_sans_aucun_resultat"]


# -- les paires que rien ne separe ----------------------------------------


def test_les_paires_trop_proches_sont_signalees(tmp_path, capsys, monkeypatch):
    """Si deux textes ne se distinguent pas, aucun index ne les distinguera.
    C'est un defaut de la bibliotheque, et la moitie negative des declencheurs
    est le mecanisme prevu pour le corriger."""
    store = Store(tmp_path / "cf.db")
    peupler(store, {
        "baoyu-comic": ("Planches dessinees style visuel narratif.",
                        [("Déclencheurs", "- « style planche visuel narratif »")]),
        "baoyu-infographic": ("Planches dessinees style visuel narratif.",
                              [("Déclencheurs", "- « style planche visuel narratif »")]),
        "gm-distinct": ("Factures fournisseurs proforma Qonto.", []),
    })
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "cf.db"))
    assert cli_main(["--json", "confusions"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["paires"] >= 1
    paire = out["detail"][0]
    assert paire["skills"] == ["baoyu-comic", "baoyu-infographic"]
    assert paire["interdiction"] == [], "ni l'un ni l'autre n'a d'interdiction"
    assert out["paires_sans_aucune_interdiction"] >= 1
    assert any("n'a pas ete utilise" in l for l in out["lecture"])


def test_une_interdiction_est_signalee_sur_la_paire(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / "ci.db")
    peupler(store, {
        "a-skill": ("Texte commun partage entierement.",
                    [("Déclencheurs", "- « commun partage »\nNe pas charger pour b.")]),
        "b-skill": ("Texte commun partage entierement.",
                    [("Déclencheurs", "- « commun partage »")]),
    })
    store.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "ci.db"))
    assert cli_main(["--json", "confusions"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["detail"][0]["interdiction"] == ["a-skill"]
    assert out["paires_sans_aucune_interdiction"] == 0
