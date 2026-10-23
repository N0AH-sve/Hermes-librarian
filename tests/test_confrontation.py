"""Tests de la confrontation GK contre gb.

C'est la mesure qui decide de la phase 3, donc elle doit etre sans complaisance
dans les deux sens : ne pas flatter GK, et ne pas desavantager gb.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk.cli import main as cli_main  # noqa: E402
from gk.confrontation import attendu_de, confronter  # noqa: E402
from gk.jugement import MARQUEUR, VERSION, relire  # noqa: E402
from gk.service import Service  # noqa: E402
from gk.store import Store  # noqa: E402


@pytest.fixture()
def svc(tmp_path):
    s = Service.open(tmp_path / "c.db")
    yield s
    s.close()


def _charge(store, session, skill, ts):
    store.execute(
        "INSERT INTO skill_uses (ts, session_id, skill_name, action, expires_at) "
        "VALUES (?, ?, ?, 'load', ?)",
        (ts, session, skill, ts + 1e9),
    )


def _ts(store, texte):
    row = store.one("SELECT ts FROM requests WHERE text_truncated = ?", (texte,))
    return row["ts"]


# -- la resolution de l'etiquette ------------------------------------------


def test_un_rang_et_un_nom_designent_le_meme_skill():
    """Les deux formes de reponse doivent se resoudre en un nom, sinon la
    confrontation ne porterait que sur la moitie de l'echantillon."""
    par_rang = {"numero": 1, "candidats": ["a", "b", "c"], "reponse": 2, "nom": None}
    par_nom = {"numero": 2, "candidats": ["a"], "reponse": None, "nom": "b"}
    assert attendu_de(par_rang) == "b"
    assert attendu_de(par_nom) == "b"
    assert attendu_de({"numero": 3, "candidats": ["a"], "reponse": 0, "nom": None}) is None


def test_un_rang_de_reprise_se_resout_sur_son_depart():
    """Une reprise commence au rang 6 : lire `6` comme le premier candidat
    designerait le mauvais skill."""
    cas = {"numero": 1, "candidats": ["f", "g"], "reponse": 7, "nom": None,
           "premier_rang": 6}
    assert attendu_de(cas) == "g"


# -- la confrontation -------------------------------------------------------


def test_gk_et_gb_sont_mesures_sur_le_meme_sous_ensemble(svc):
    """Comparer GK sur cinquante cas et gb sur trente serait sans valeur."""
    svc.request("s1", "la facture proforma ne colle pas")
    svc.request("s2", "quelles commandes sont a perte")
    t1 = _ts(svc.store, "la facture proforma ne colle pas")
    t2 = _ts(svc.store, "quelles commandes sont a perte")
    # gb charge le bon skill sur s1, le mauvais sur s2.
    _charge(svc.store, "s1", "gm-factures", t1 + 10)
    _charge(svc.store, "s2", "gm-autre", t2 + 10)

    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. la facture proforma ne colle pas\n"
        "   1. gm-factures   [greenmusic]  score 9\n"
        f"{MARQUEUR} 1\n"
        "\n"
        "## 2. quelles commandes sont a perte\n"
        "   1. gm-factures   [greenmusic]  score 2\n"
        "   2. gm-marges   [greenmusic]  score 1\n"
        f"{MARQUEUR} 2\n"
    )
    out = confronter(svc.store, cas)
    assert out["comparables"] == 2
    # GK : juste sur 1, faux sur 2 (son rang 1 est gm-factures).
    assert out["gk_rappel_1"] == 0.5
    # gb : juste sur 1, faux sur 2.
    assert out["gb_rappel_session"] == 0.5
    assert out["ecart_gk_moins_gb"] == 0.0


def test_la_mesure_de_session_avantage_gb(svc):
    """gb charge surtout en debut de session : une fenetre apres la demande
    rendrait zero et ecraserait gb a tort."""
    svc.request("s1", "une premiere demande")
    svc.request("s1", "et une seconde bien plus tard")
    t1 = _ts(svc.store, "une premiere demande")
    # Le chargement precede la seconde demande : hors de sa fenetre.
    _charge(svc.store, "s1", "gm-bon", t1 + 5)

    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. et une seconde bien plus tard\n"
        "   1. gm-autre   [c]  score 3\n"
        f"{MARQUEUR} gm-bon\n"
    )
    out = confronter(svc.store, cas, fenetre=1.0)
    assert out["gb_rappel_fenetre"] == 0.0, "la fenetre ne le voit pas"
    assert out["gb_rappel_session"] == 1.0, "la session le credite quand meme"


def test_une_abstention_de_gb_est_comptee_a_part(svc):
    """Confondre « gb s'est trompe » et « gb n'a rien charge » rendrait le
    chiffre inutilisable : les deux ont des remedes opposes."""
    svc.request("s9", "une demande que gb n'a pas servie")
    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. une demande que gb n'a pas servie\n"
        "   1. gm-bon   [c]  score 5\n"
        f"{MARQUEUR} 1\n"
    )
    out = confronter(svc.store, cas)
    assert out["gb_sans_aucun_chargement"] == 1
    assert out["gb_rappel_session"] == 0.0
    assert out["gk_rappel_1"] == 1.0


def test_un_cas_introuvable_en_base_est_exclu_des_deux_cotes(svc):
    """Sinon l'exclusion creerait un biais en faveur de l'un des deux."""
    svc.request("s1", "une demande connue")
    t1 = _ts(svc.store, "une demande connue")
    _charge(svc.store, "s1", "gm-bon", t1 + 5)
    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. une demande connue\n   1. gm-bon   [c]  score 5\n"
        f"{MARQUEUR} 1\n"
        "\n"
        "## 2. une demande jamais enregistree\n   1. gm-bon   [c]  score 5\n"
        f"{MARQUEUR} 1\n"
    )
    out = confronter(svc.store, cas)
    assert out["comparables"] == 1 and out["non_apparies"] == 1
    assert out["gk_rappel_1"] == 1.0 and out["gb_rappel_session"] == 1.0


def test_une_demande_multiligne_est_appariee_par_prefixe_et_comptee(svc):
    """Un echantillon ancien ne porte que la premiere ligne dans son titre.

    L'approximation est admise, mais elle se compte : une approximation cachee
    serait pire que pas de mesure.
    """
    svc.request("s1", "premiere ligne de la demande\nseconde ligne")
    row = svc.store.one("SELECT ts FROM requests LIMIT 1")
    _charge(svc.store, "s1", "gm-bon", row["ts"] + 5)
    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. premiere ligne de la demande\n   1. gm-bon   [c]  score 5\n"
        f"{MARQUEUR} 1\n"
    )
    out = confronter(svc.store, cas)
    assert out["comparables"] == 1
    assert out["apparies_par_prefixe"] == 1


def test_la_confrontation_refuse_un_echantillon_sans_skill_designe(svc):
    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. une demande\n   1. gm-bon   [c]  score 5\n"
        f"{MARQUEUR} 0\n"
    )
    out = confronter(svc.store, cas)
    assert out["ok"] is False and "rien a confronter" in out["erreur"]


# -- la commande ------------------------------------------------------------


def test_gk_confronter_rend_les_deux_chiffres(tmp_path, capsys, monkeypatch):
    svc = Service.open(tmp_path / "x.db")
    svc.request("s1", "une demande servie")
    row = svc.store.one("SELECT ts FROM requests LIMIT 1")
    svc.store.execute(
        "INSERT INTO skill_uses (ts, session_id, skill_name, action, expires_at) "
        "VALUES (?, 's1', 'gm-bon', 'load', ?)",
        (row["ts"] + 5, row["ts"] + 1e9),
    )
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "x.db"))
    faux = (
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. une demande servie\n   1. gm-bon   [c]  score 5\n"
        f"{MARQUEUR} 1\n"
    )
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(faux))
    assert cli_main(["--json", "confronter"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["gk_rappel_1"] == 1.0 and out["gb_rappel_session"] == 1.0
    assert "details" not in out, "le cas par cas demande --details"
    assert any("AVANTAGE gb" in l for l in out["lecture"])
    assert any("CORRELATION" in l for l in out["lecture"])


def test_un_skill_designe_hors_catalogue_est_ecarte(svc):
    """La resolution d'un rang en nom passe par les candidats relus du fichier.

    Sur un echantillon v1, une ligne du texte de l'utilisateur peut ressembler
    a un candidat et decaler la liste. Le chiffre serait alors faux sans rien
    signaler, juste avant de porter une decision.
    """
    from gk.catalog import Catalog
    Catalog(svc.store).ingest([
        {"path": "a/gm-bon/SKILL.md", "name": "gm-bon", "description": "d",
         "category": "greenmusic", "fingerprint": "1"},
    ], complete=True)
    svc.request("s1", "une demande connue")
    svc.request("s2", "une seconde demande connue")
    row = svc.store.one("SELECT ts FROM requests ORDER BY ts LIMIT 1")
    _charge(svc.store, "s1", "gm-bon", row["ts"] + 5)

    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. une demande connue\n   1. gm-bon   [greenmusic]  score 5\n"
        f"{MARQUEUR} 1\n"
        "\n"
        "## 2. une seconde demande connue\n"
        "   1. pas-un-skill   [?]  score 1\n"
        f"{MARQUEUR} 1\n"
    )
    out = confronter(svc.store, cas)
    assert out["ok"] is True
    assert out["designes_hors_catalogue"] == 1
    assert out["comparables"] == 1, "seul le cas valide est compte"
    assert any("derape" in l for l in __import__(
        "gk.confrontation", fromlist=["lecture"]).lecture(out))


def test_une_relecture_massivement_deraillee_refuse_de_conclure(svc):
    """Un quart de cas invalides n'est plus un accident : c'est un format mal
    relu, et conclure dessus serait pire que ne rien rendre."""
    from gk.catalog import Catalog
    Catalog(svc.store).ingest([
        {"path": "a/gm-bon/SKILL.md", "name": "gm-bon", "description": "d",
         "category": "greenmusic", "fingerprint": "1"},
    ], complete=True)
    for i in range(1, 5):
        svc.request("s1", f"demande numero {i}")
    texte = f"<!-- gk echantillon v{VERSION} -->\n"
    for i in range(1, 5):
        texte += (
            f"## {i}. demande numero {i}\n"
            f"   1. inconnu-{i}   [?]  score 1\n"
            f"{MARQUEUR} 1\n\n"
        )
    out = confronter(svc.store, relire(texte))
    assert out["ok"] is False
    assert "derape" in out["erreur"]
    assert out["exemples"]


def test_les_deux_courbes_de_rang_sont_comparees_au_meme_rang(svc):
    """gb charge neuf skills par session : comparer le rang 1 de GK a ce lot
    etait une erreur de denominateur.

    La courbe de gb est l'ordre de ses chargements entre cette demande et la
    suivante -- son premier choix, pas son ratissage.
    """
    from gk.catalog import Catalog
    Catalog(svc.store).ingest([
        {"path": f"a/s{i}/SKILL.md", "name": f"s{i}", "description": "d",
         "category": "c", "fingerprint": "1"} for i in range(1, 5)
    ], complete=True)
    svc.request("s1", "la demande qui nous interesse")
    svc.request("s1", "une demande posterieure")
    t1 = svc.store.one(
        "SELECT ts FROM requests WHERE text_truncated = ?",
        ("la demande qui nous interesse",),
    )["ts"]
    # Les deux demandes doivent etre espacees comme dans une vraie
    # conversation : la fenetre d'un tour est bornee par la demande suivante.
    svc.store.execute(
        "UPDATE requests SET ts = ? WHERE text_truncated = ?",
        (t1 + 600, "une demande posterieure"),
    )
    # gb charge s3 puis s2 : le bon skill (s2) est son SECOND choix.
    _charge(svc.store, "s1", "s3", t1 + 1)
    _charge(svc.store, "s1", "s2", t1 + 2)

    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. la demande qui nous interesse\n"
        "   1. s1   [c]  score 9\n"
        "   2. s2   [c]  score 7\n"
        f"{MARQUEUR} 2\n"
    )
    out = confronter(svc.store, cas)
    # Le bon skill est au rang 2 pour les deux.
    assert out["courbe_gk"]["1"] == 0.0 and out["courbe_gk"]["2"] == 1.0
    assert out["courbe_gb"]["1"] == 0.0 and out["courbe_gb"]["2"] == 1.0
    assert out["details"][0]["gk_rang_du_bon"] == 2
    assert out["details"][0]["gb_rang_du_bon"] == 2


def test_la_sequence_de_gb_s_arrete_a_la_demande_suivante(svc):
    """Un chargement posterieur a la demande suivante repond a celle-la, pas a
    celle-ci : l'y compter attribuerait a gb un choix qu'il n'a pas fait."""
    from gk.catalog import Catalog
    Catalog(svc.store).ingest([
        {"path": "a/bon/SKILL.md", "name": "bon", "description": "d",
         "category": "c", "fingerprint": "1"},
    ], complete=True)
    svc.request("s1", "la premiere demande")
    svc.request("s1", "la demande suivante")
    t1 = svc.store.one(
        "SELECT ts FROM requests WHERE text_truncated = ?",
        ("la premiere demande",),
    )["ts"]
    svc.store.execute(
        "UPDATE requests SET ts = ? WHERE text_truncated = ?",
        (t1 + 600, "la demande suivante"),
    )
    # Le chargement a lieu APRES la demande suivante.
    _charge(svc.store, "s1", "bon", t1 + 601)

    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. la premiere demande\n   1. bon   [c]  score 9\n"
        f"{MARQUEUR} 1\n"
    )
    out = confronter(svc.store, cas)
    assert out["courbe_gb"]["1"] == 0.0, "le chargement n'est pas attribue ici"
    assert out["gb_rappel_session"] == 1.0, "mais la session le voit toujours"


def test_un_skill_recharge_ne_compte_qu_une_fois_dans_la_sequence(svc):
    """C'est un rang, pas un volume : un rechargement ne doit pas repousser le
    bon skill d'un cran."""
    from gk.catalog import Catalog
    Catalog(svc.store).ingest([
        {"path": "a/x/SKILL.md", "name": "x", "description": "d",
         "category": "c", "fingerprint": "1"},
        {"path": "a/bon/SKILL.md", "name": "bon", "description": "d",
         "category": "c", "fingerprint": "1"},
    ], complete=True)
    svc.request("s1", "une demande")
    t1 = svc.store.one("SELECT ts FROM requests LIMIT 1")["ts"]
    _charge(svc.store, "s1", "x", t1 + 1)
    _charge(svc.store, "s1", "x", t1 + 2)
    _charge(svc.store, "s1", "bon", t1 + 3)

    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. une demande\n   1. x   [c]  score 9\n   2. bon   [c]  score 7\n"
        f"{MARQUEUR} 2\n"
    )
    out = confronter(svc.store, cas)
    assert out["details"][0]["gb_rang_du_bon"] == 2, "et non 3"
