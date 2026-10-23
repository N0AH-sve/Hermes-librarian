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
from gk.jugement import (  # noqa: E402
    MARQUEUR,
    VERSION,
    juger,
    preparer,
    reprise,
    relire,
)
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
    assert "le NUMERO du bon skill" in texte
    assert "`?` si vous ne savez pas" in texte
    # La troisieme reponse est celle qui porte la mesure : sans elle, le rappel
    # dans les cinq vaut mecaniquement l'inverse du taux de `0`.
    assert "le NOM du bon skill" in texte
    assert f"<!-- gk echantillon v{VERSION} -->" in texte


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


def _cas(numero, reponse=None, nom=None, candidats=("x",), version=VERSION):
    return {
        "numero": numero, "demande": f"d{numero}", "candidats": list(candidats),
        "reponse": reponse, "nom": nom, "brut": "", "version": version,
    }


def test_le_jugement_rend_le_rappel_reel():
    """Le denominateur est le nombre de demandes qu'un skill peut servir.

    Une demande que rien ne sert n'est pas un echec de recherche, et la compter
    abaisserait le chiffre sans rien apprendre.
    """
    cas = [
        _cas(1, reponse=1),
        _cas(2, reponse=1),
        _cas(3, reponse=4),
        _cas(4, nom="gm-ailleurs"),          # existe, hors des cinq
        _cas(5, reponse=0),                  # trou de la bibliotheque
        _cas(6, reponse=None),               # « ? »
    ]
    out = juger(cas)
    assert out["cas_annotes"] == 5 and out["non_annotes"] == 1
    assert out["demandes_servables"] == 4, "les `0` sortent du denominateur"
    assert out["rappel_1_reel"] == 0.5, "deux premiers sur quatre servables"
    assert out["rappel_5_reel"] == 0.75, "trois des quatre dans les cinq"
    assert out["aucun_skill_ne_convient"] == 1
    assert out["designe_hors_des_5"] == 1


def test_le_rappel_5_n_est_pas_l_inverse_du_taux_de_zero():
    """Le defaut de la v1, tenu par un test.

    En v1, toute reponse non nulle designait un des cinq par construction, donc
    `rappel_5` valait exactement `1 - part_sans_reponse`. Le chiffre ne portait
    aucune information. Un rappel@5 qui bouge a nombre de `0` constant est la
    preuve que la v2 mesure autre chose.
    """
    base = [_cas(i, reponse=1) for i in range(1, 4)] + [_cas(4, reponse=0)]
    tout_dans_les_cinq = juger(base)
    un_hors_des_cinq = juger(base[:2] + [_cas(3, nom="gm-ailleurs"), base[3]])

    # Meme nombre de `0`, meme nombre de cas annotes.
    assert (
        tout_dans_les_cinq["aucun_skill_ne_convient"]
        == un_hors_des_cinq["aucun_skill_ne_convient"]
    )
    assert tout_dans_les_cinq["cas_annotes"] == un_hors_des_cinq["cas_annotes"]
    # Et pourtant le rappel@5 bouge : il mesure donc la recherche.
    assert tout_dans_les_cinq["rappel_5_reel"] == 1.0
    assert un_hors_des_cinq["rappel_5_reel"] < 1.0


def test_un_echantillon_v1_est_rendu_en_intervalle():
    """Un chiffre faux coute plus cher qu'un chiffre absent.

    Sur un echantillon v1, les `0` melangent les trous de la bibliotheque et
    les skills simplement manques : le denominateur est inconnu, donc le
    resultat est un intervalle et le point n'est pas annonce.
    """
    cas = [_cas(i, reponse=1, version=1) for i in range(1, 4)]
    cas += [_cas(i, reponse=0, version=1) for i in range(4, 8)]
    out = juger(cas)
    assert "rappel_1_reel" not in out and "rappel_5_reel" not in out
    assert out["rappel_1_borne_basse"] == round(3 / 7, 3)
    assert out["rappel_1_borne_haute"] == 1.0
    assert out["ambigus"] == 4
    assert "ne distinguent pas" in out["avertissement"]


def test_un_nom_deja_affiche_revient_a_son_rang():
    """Ecrire le nom d'un candidat montre n'est pas un echec de rappel.

    Sinon l'annotateur qui recopie un nom au lieu de son numero gonflerait les
    echecs hors des cinq.
    """
    cas = relire(
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. une demande\n"
        "   1. gm-factures   [greenmusic]  score 9\n"
        "   2. gm-marges   [greenmusic]  score 3\n"
        f"{MARQUEUR} gm-marges\n"
    )
    assert cas[0]["reponse"] == 2 and cas[0]["nom"] is None
    assert juger(cas)["designe_hors_des_5"] == 0


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
    faux = f"<!-- gk echantillon v{VERSION} -->\n" + "\n".join(
        f"## {i}. une demande\n   1. un-skill   [c]  score 1\n{MARQUEUR} 0"
        for i in range(1, 5)
    )
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(faux))
    assert cli_main(["--json", "juger"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["part_sans_reponse_possible"] == 1.0
    assert any("trou de la" in l for l in out["lecture"])


def test_gk_juger_refuse_d_annoncer_un_point_sur_un_echantillon_v1(
    tmp_path, capsys, monkeypatch
):
    """La lecture d'un echantillon v1 met l'ambiguite AVANT les chiffres.

    C'est le vrai defaut du premier releve : la commande annoncait
    « rappel dans les cinq : 0,638 » et le lecteur n'avait aucun moyen de voir
    que ce chiffre etait l'inverse arithmetique du taux de `0`.
    """
    Store(tmp_path / "z.db").close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "z.db"))
    faux = "\n".join(
        f"## {i}. une demande\n   1. un-skill   [c]  score 1\n{MARQUEUR} "
        + ("1" if i <= 2 else "0")
        for i in range(1, 6)
    )
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(faux))
    assert cli_main(["--json", "juger"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "rappel_5_reel" not in out
    assert out["lecture"][2].startswith("AMBIGU")
    assert any("gk reprise" in l for l in out["lecture"])


# -- la reprise -------------------------------------------------------------


def test_la_reprise_ne_reprend_que_les_cas_ambigus(prepare):
    """Seuls les cas `0` portent la difference : reprendre les cinquante
    gaspillerait l'heure deja passee."""
    svc, index = prepare
    cas = relire(
        "## 1. la facture proforma du fournisseur ne colle pas\n"
        "   1. gm-factures   [greenmusic]  score 9\n"
        f"{MARQUEUR} 1\n"
        "\n"
        "## 2. quelles commandes sont a perte\n"
        "   1. gm-factures   [greenmusic]  score 2\n"
        f"{MARQUEUR} 0\n"
    )
    texte = reprise(svc.store, index, cas)
    assert "## 2." in texte, "le cas ambigu est repris"
    assert "## 1." not in texte, "le cas deja tranche ne l'est pas"
    # Les rangs montres sont les rangs REELS : la suite du meme classement.
    assert "   2. " in texte and "   1. " not in texte
    assert f"<!-- gk echantillon v{VERSION} -->" in texte


def test_la_reprise_corrige_le_premier_fichier(prepare):
    """Les deux fichiers se relisent concatenes, et le second gagne.

    Sans cette regle, lever l'ambiguite demanderait de refaire l'annotation
    entiere.
    """
    svc, index = prepare
    premier = (
        "## 1. quelles commandes sont a perte\n"
        "   1. gm-factures   [greenmusic]  score 2\n"
        f"{MARQUEUR} 0\n"
    )
    seconde = (
        f"<!-- gk echantillon v{VERSION} -->\n"
        "## 1. quelles commandes sont a perte\n"
        "   2. gm-marges   [greenmusic]  score 7\n"
        f"{MARQUEUR} 2\n"
    )
    cas = relire(premier + seconde)
    assert len(cas) == 1, "un seul cas, pas deux"
    assert cas[0]["reponse"] == 2, "la seconde passe l'emporte"
    out = juger(cas)
    assert out["aucun_skill_ne_convient"] == 0
    assert out["designe_hors_des_5"] == 0


def test_la_reprise_sans_cas_ambigu_le_dit(prepare):
    svc, index = prepare
    cas = relire(
        "## 1. une demande\n   1. gm-factures   [greenmusic]  score 1\n"
        f"{MARQUEUR} 1\n"
    )
    assert "rien a reprendre" in reprise(svc.store, index, cas)


def test_une_demande_multiligne_ne_casse_pas_le_format(prepare):
    """Le format porte la demande dans un titre `## N. ...`.

    Un message d'utilisateur peut contenir des retours a la ligne, et sans
    repli ses lignes suivantes peuvent etre prises pour des candidats.
    """
    svc, index = prepare
    svc.request("s3", "premiere ligne\n   2. pas un candidat\nderniere ligne")
    index = Index.construire(svc.store)
    texte = preparer(svc.store, index, taille=20)
    cas = relire(texte)
    multi = [c for c in cas if "premiere ligne" in c["demande"]]
    assert multi, "la demande multiligne est bien dans l'echantillon"
    assert "derniere ligne" in multi[0]["demande"], "repliee sur une ligne"
    assert "pas un candidat" not in multi[0]["candidats"]


def test_gk_catalogue_noms_liste_les_skills(tmp_path, capsys, monkeypatch):
    """Pour annoter, il faut pouvoir retrouver un nom : personne ne tient 186
    noms de tete."""
    svc = Service.open(tmp_path / "n.db")
    Catalog(svc.store).ingest([
        {"path": "a/gm-un/SKILL.md", "name": "gm-un", "description": "d",
         "category": "greenmusic", "fingerprint": "1"},
    ], complete=True)
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "n.db"))
    assert cli_main(["--json", "catalogue", "--noms"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skills"] == ["gm-un"]


def test_le_rang_du_bon_skill_est_rendu_quand_il_est_cite():
    """Le rang separe un probleme de classement d'un probleme de recherche.

    Le bon skill au rang 2 se corrige par des coefficients ; au rang 5, a
    peine. Un rappel@5 global ne distingue pas les deux.
    """
    cas = [_cas(1, reponse=1), _cas(2, reponse=1), _cas(3, reponse=2),
           _cas(4, reponse=5), _cas(5, reponse=0)]
    out = juger(cas)
    assert out["distribution_des_rangs"] == {"1": 2, "2": 1, "5": 1}
    # Le taux de citation sur TOUT : le denominateur que voit un brief, qui ne
    # sait pas si une demande a une reponse.
    assert out["cite_dans_les_5_sur_tout"] == 0.8
    assert out["part_premier_quand_cite"] == 0.5


def test_le_taux_de_citation_ne_depend_d_aucune_hypothese():
    """Contrairement au rappel, il se compte et ne s'infere pas : il est donc
    rendu meme sur un echantillon v1 ambigu."""
    cas = [_cas(i, reponse=1, version=1) for i in range(1, 4)]
    cas += [_cas(i, reponse=0, version=1) for i in range(4, 8)]
    out = juger(cas)
    assert out["cite_dans_les_5_sur_tout"] == round(3 / 7, 3)
    assert out["part_premier_quand_cite"] == 1.0


# -- les injections du systeme ---------------------------------------------


def test_une_banniere_du_systeme_n_est_pas_une_demande():
    """Releve par gb le 2026-10-23 : 53 des 196 demandes d'une journee sont des
    injections du systeme.

    `pre_llm_call` porte le message tel que le modele le recoit, donc ces blocs
    y sont. Les juger fausse le rappel -- aucun skill ne sert une banniere -- et
    surtout la part « aucun ne convient », qui en herite directement.
    """
    from gk.jugement import est_injection

    assert est_injection("Skill invoked: gm-factures")
    assert est_injection("<system-reminder>pense a ceci</system-reminder>")
    assert est_injection("Background process completed")
    assert est_injection("[Attachment: facture.pdf]")
    assert est_injection("   ")
    # Et une vraie demande n'est pas attrapee.
    assert not est_injection("pourquoi on a paye 40 balles de port chez Adagio")
    assert not est_injection("le fournisseur nous facture le port, c'est normal ?")


def test_l_echantillon_ecarte_les_injections_et_le_dit(prepare):
    """Le filtre doit etre visible dans le fichier : un motif trop large qui
    mange des demandes reelles doit se voir, pas disparaitre."""
    svc, _ = prepare
    svc.request("s4", "Skill invoked: gm-factures")
    svc.request("s4", "Background process completed")
    index = Index.construire(svc.store)
    texte = preparer(svc.store, index, taille=20)
    assert "injections du systeme ecartees" in texte
    assert "Skill invoked" not in texte
    assert "Background process completed" not in texte


def test_gk_demandes_compte_les_injections(tmp_path, capsys, monkeypatch):
    svc = Service.open(tmp_path / "i.db")
    svc.request("s1", "une vraie demande de collegue")
    svc.request("s1", "Skill invoked: quelque-chose")
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(tmp_path / "i.db"))
    assert cli_main(["--json", "demandes", "--injections", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["injections_systeme"] == 1
    assert out["demandes_de_collegues"] == 1
    assert out["exemples_injections"] == ["Skill invoked: quelque-chose"]
