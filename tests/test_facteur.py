"""Tests du facteur : manifeste et tranches, cote pont.

Ce code vit dans le volume de production de l'agent et tourne a chaque tour. Il
ne peut pas etre teste la-bas, donc il est teste ici, et aucun de ces tests ne
demande Hermes.

Deux proprietes comptent plus que les autres, parce que leur violation serait
silencieuse :

* une tranche est renvoyee **meme quand aucun intitule ne correspond**, sinon GK
  la reclame a chaque manifeste, pour toujours ;
* un filtre qui ne garderait rien est **ignore**, sinon une fonction privee de
  Hermes qui change de forme viderait le catalogue entier.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

RACINE_DEPOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_DEPOT / "server"))

from gk.service import Service  # noqa: E402


def _charger(nom: str, fichier: str):
    spec = importlib.util.spec_from_file_location(
        nom, RACINE_DEPOT / "hermes-plugin" / "gk-bridge" / fichier
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


cat = _charger("gkb_catalogue", "catalogue.py")
pont = _charger("gkb_pont", "__init__.py")


SKILL = """---
name: gm-presta-commande
description: "Passer une commande fournisseur sur le B2B Adagio"
version: 3
---
# gm-presta-commande

## Déclencheurs
Charger pour une commande fournisseur.
Ne pas charger pour une commande client : voir gm-presta-catalogue.

## Procédure
```bash
# ceci n'est pas un titre
## celui-ci non plus
./bin/commander
```

### Ne pas charger si
La demande porte sur un avoir.

## Pièges
Ressemble a une commande client.
"""


def ecrire(racine: Path, chemin: str, texte: str = SKILL) -> Path:
    cible = racine / chemin / "SKILL.md"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(texte, encoding="utf-8")
    return cible


# -- le decoupage ----------------------------------------------------------


def test_le_decoupage_prend_les_intitules_demandes_a_tous_les_niveaux():
    tranches = cat.trancher(SKILL, ["Déclencheurs", "Ne pas charger si"])
    titres = [t["heading"] for t in tranches]
    assert titres == ["Déclencheurs", "Ne pas charger si"]
    assert "commande fournisseur" in tranches[0]["text"]
    assert "avoir" in tranches[1]["text"]


def test_un_diese_dans_un_bloc_de_code_n_est_pas_un_titre():
    """L'erreur frapperait precisement les skills qui portent des procedures."""
    tranches = cat.trancher(SKILL, ["Procédure"])
    assert len(tranches) == 1
    assert "ceci n'est pas un titre" in tranches[0]["text"]
    assert "celui-ci non plus" in tranches[0]["text"]


def test_le_decoupage_tolere_les_accents_la_casse_et_une_glose():
    texte = "## DECLENCHEURS    quand charger / quand NE PAS charger\ncorps\n"
    tranches = cat.trancher(texte, ["Déclencheurs"])
    assert len(tranches) == 1 and tranches[0]["text"] == "corps"


def test_sans_intitule_demande_le_decoupage_ne_rend_rien():
    assert cat.trancher(SKILL, []) == []
    assert cat.trancher(SKILL, ["Rubrique inexistante"]) == []


def test_une_tranche_s_arrete_au_titre_suivant():
    tranches = cat.trancher(SKILL, ["Déclencheurs"])
    assert "Procédure" not in tranches[0]["text"]


# -- le frontmatter --------------------------------------------------------


def test_le_frontmatter_rend_le_nom_et_la_description(tmp_path):
    chemin = ecrire(tmp_path, "cat/skill")
    nom, desc = cat.frontmatter(str(chemin))
    assert nom == "gm-presta-commande"
    assert desc == "Passer une commande fournisseur sur le B2B Adagio"


def test_un_fichier_sans_frontmatter_ne_leve_pas(tmp_path):
    chemin = ecrire(tmp_path, "cat/plat", "## Déclencheurs\nx\n")
    assert cat.frontmatter(str(chemin)) == ("", "")
    assert cat.frontmatter(str(tmp_path / "absent.md")) == ("", "")


# -- le manifeste ----------------------------------------------------------


def test_le_manifeste_porte_chemin_relatif_empreinte_et_categorie(tmp_path):
    ecrire(tmp_path, "prestashop/commande")
    entrees, complet, diag = cat.manifeste(str(tmp_path))
    assert complet is True
    assert len(entrees) == 1
    entree = entrees[0]
    assert entree["path"] == os.path.join("prestashop", "commande", "SKILL.md")
    assert entree["category"] == "prestashop"
    assert entree["name"] == "gm-presta-commande"
    taille, _, mtime = entree["fingerprint"].partition(",")
    assert taille.isdigit() and mtime.isdigit(), "(taille, mtime), pas un hachage"


def test_le_nom_retombe_sur_le_dossier_sans_frontmatter(tmp_path):
    ecrire(tmp_path, "cat/nom-du-dossier", "## Déclencheurs\nx\n")
    entrees, _, _ = cat.manifeste(str(tmp_path))
    assert entrees[0]["name"] == "nom-du-dossier"


def test_un_filtre_hermes_qui_ne_garderait_rien_est_ignore(tmp_path, monkeypatch):
    """Sinon une fonction privee qui change de forme viderait le catalogue."""
    ecrire(tmp_path, "cat/skill")
    monkeypatch.setattr(
        cat, "lister_par_hermes", lambda: ({"un-skill-qui-n-existe-pas": ""}, "factice")
    )
    entrees, _, diag = cat.manifeste(str(tmp_path))
    assert len(entrees) == 1, "le filtre est ignore plutot que de tout jeter"
    assert "filtre ignore" in diag["hermes"]


def test_un_filtre_hermes_valide_restreint_et_complete(tmp_path, monkeypatch):
    ecrire(tmp_path, "cat/garde")
    ecrire(tmp_path, "cat/jete", "## Déclencheurs\nx\n")
    monkeypatch.setattr(
        cat, "lister_par_hermes",
        lambda: ({"gm-presta-commande": "description venue de Hermes"}, "factice"),
    )
    entrees, _, diag = cat.manifeste(str(tmp_path))
    assert [e["name"] for e in entrees] == ["gm-presta-commande"]
    assert diag["filtres"] == 1


def test_hermes_complete_une_description_manquante(tmp_path, monkeypatch):
    ecrire(tmp_path, "cat/sans-desc", "---\nname: gm-x\n---\n## Déclencheurs\nx\n")
    monkeypatch.setattr(
        cat, "lister_par_hermes", lambda: ({"gm-x": "venue de Hermes"}, "factice")
    )
    entrees, _, _ = cat.manifeste(str(tmp_path))
    assert entrees[0]["description"] == "venue de Hermes"


def test_le_parcours_est_borne(tmp_path, monkeypatch):
    monkeypatch.setattr(cat, "MAX_FICHIERS", 2)
    for i in range(5):
        ecrire(tmp_path, f"cat/s{i}")
    entrees, complet, _ = cat.manifeste(str(tmp_path))
    assert len(entrees) <= 2
    # Tronque, donc PAS complet : sans cela GK ferait disparaitre le reste.
    assert complet is False


# -- les tranches demandees ------------------------------------------------


def test_une_entree_figure_meme_sans_intitule_correspondant(tmp_path):
    """Sinon GK la reclame a chaque manifeste, pour toujours."""
    ecrire(tmp_path, "cat/skill", "## Autre chose\nx\n")
    chemin = os.path.join("cat", "skill", "SKILL.md")
    sorties = cat.tranches_pour(str(tmp_path), [chemin], ["Déclencheurs"])
    assert len(sorties) == 1
    assert sorties[0]["sections"] == []
    assert sorties[0]["fingerprint"]


def test_un_fichier_disparu_rend_une_entree_vide(tmp_path):
    sorties = cat.tranches_pour(str(tmp_path), ["cat/fantome/SKILL.md"], ["Déclencheurs"])
    assert sorties == [{"path": "cat/fantome/SKILL.md", "fingerprint": "", "sections": []}]


def test_un_chemin_qui_sort_de_la_racine_est_refuse(tmp_path):
    (tmp_path / "dehors").mkdir()
    (tmp_path / "dehors" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    racine = tmp_path / "skills"
    racine.mkdir()
    sorties = cat.tranches_pour(str(racine), ["../dehors/SKILL.md"], ["Déclencheurs"])
    assert sorties == []


# -- la lecture du resultat MCP -------------------------------------------


CHARGE = {"v": 1, "ok": True, "wanted": ["a"], "headings": ["D"], "besoins": []}


def test_extraire_defait_l_enveloppe_de_hermes():
    """La forme reelle, relevee dans `PluginContext._mcp_envelope`.

    C'est le defaut qui a fait echouer la premiere ronde : la sonde comprenait
    `ok`, qui appartient a l'enveloppe, donc l'enveloppe etait rendue a la place
    de la charge utile et `wanted` valait None.
    """
    assert pont._extraire({"ok": True, "result": CHARGE}) == CHARGE
    assert pont._extraire({"ok": True, "result": CHARGE, "truncated": True}) == CHARGE
    assert pont._extraire(
        {"ok": True, "result": {"autre": 1}, "structuredContent": CHARGE}
    ) == {"autre": 1}, "result d'abord, il est toujours la"


def test_extraire_accepte_aussi_les_formes_de_repli():
    assert pont._extraire(CHARGE) == CHARGE, "charge directe, pour les tests"
    assert pont._extraire({"structuredContent": CHARGE}) == CHARGE
    assert pont._extraire({"content": [{"type": "text", "text": json.dumps(CHARGE)}]}) == CHARGE
    assert pont._extraire({"ok": True, "result": json.dumps(CHARGE)}) == CHARGE


def test_extraire_rend_vide_sur_une_erreur_rapportee():
    assert pont._extraire({"ok": False, "error": "gk est tombe"}) == {}
    assert pont._extraire(None) == {}


def test_une_extraction_vide_est_journalisee(tmp_path, monkeypatch):
    """Le trou du premier jet : la forme n'etait consignee que sur exception,
    pas quand l'extraction rendait {} en silence -- le cas qui est arrive."""
    monkeypatch.setattr(pont, "JOURNAL", str(tmp_path / "j.jsonl"))
    monkeypatch.setattr(pont, "_forme_relevee", False)
    assert pont._extraire({"inconnu": 1}) == {}
    lignes = [json.loads(l) for l in open(tmp_path / "j.jsonl", encoding="utf-8")]
    assert any(l["event"] == "forme_inconnue" for l in lignes)
    assert any("inconnu" in (l.get("cles") or []) for l in lignes)


# -- la ronde complete, de bout en bout -----------------------------------


class CtxFactice:
    """Un `PluginContext` minimal qui route vers un vrai Service."""

    def __init__(self, svc: Service):
        self.svc = svc
        self.appels: list[str] = []

    def call_mcp(self, serveur, outil, arguments, timeout=None):
        """Rend la charge utile DANS l'enveloppe de Hermes.

        Le test de bout en bout passait avant le correctif parce qu'il rendait
        la charge nue : il ne reproduisait pas la production, donc il ne pouvait
        pas voir le defaut. C'est le genre de test qui rassure a tort.
        """
        self.appels.append(outil)
        assert serveur == "gk"
        if outil == "catalog":
            charge = self.svc.catalog(arguments["skills"], arguments.get("complete", False))
        elif outil == "sections":
            charge = self.svc.sections(arguments["items"])
        else:
            raise AssertionError(f"outil inattendu: {outil}")
        return {"ok": True, "result": charge}


@pytest.fixture()
def ronde(tmp_path, monkeypatch):
    """Prepare une bibliotheque, un GK reel, et un pont branche sur lui."""
    racine = tmp_path / "skills"
    ecrire(racine, "prestashop/commande")
    ecrire(racine, "compta/tva", "---\nname: gm-compta-tva\ndescription: \"TVA\"\n---\n## When to use\nTaux de TVA incorrect.\n")
    svc = Service.open(tmp_path / "gk.db")
    ctx = CtxFactice(svc)
    monkeypatch.setattr(pont, "_ctx", ctx)
    monkeypatch.setattr(pont, "_dernier_catalogue", 0.0)
    monkeypatch.setattr(pont, "JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setattr(cat, "RACINE_DEFAUT", str(racine))
    monkeypatch.setattr(pont, "_catalogue_mod", cat)
    yield svc, ctx, racine, tmp_path
    svc.close()


def test_une_ronde_remplit_le_catalogue_et_les_tranches(ronde):
    svc, ctx, _, tmp = ronde
    pont._ronde_catalogue(force=True)
    etat = svc.catalog_.stats()
    assert etat["skills"] == 2
    assert etat["a_indexer"] == 0, "tout a ete indexe dans la meme ronde"
    assert etat["skills_avec_sections"] >= 1
    assert ctx.appels[0] == "catalog" and "sections" in ctx.appels
    # Le journal doit permettre de lire ce qui s'est passe, pas de le deviner.
    lignes = [json.loads(l) for l in open(tmp / "journal.jsonl", encoding="utf-8")]
    resume = [l for l in lignes if l.get("event") == "ronde_catalogue"][-1]
    assert resume["manifeste_envoye"] == 2
    assert resume["tranches_envoyees"] == 2
    assert resume["restants"] == 0


def test_les_declencheurs_des_deux_langues_sont_captures(ronde):
    svc, _, _, _ = ronde
    pont._ronde_catalogue(force=True)
    titres = {
        r["heading"] for r in svc.store.query("SELECT heading FROM skill_sections")
    }
    assert "Déclencheurs" in titres
    assert "When to use" in titres, "la bibliotheque est bilingue"


def test_une_seconde_ronde_ne_redemande_rien(ronde):
    svc, ctx, _, _ = ronde
    pont._ronde_catalogue(force=True)
    ctx.appels.clear()
    pont._dernier_catalogue = 0.0
    pont._ronde_catalogue(force=True)
    assert ctx.appels == ["catalog"], "rien n'a bouge, donc aucune tranche redemandee"


def test_une_modification_est_reprise_a_la_ronde_suivante(ronde):
    svc, ctx, racine, _ = ronde
    pont._ronde_catalogue(force=True)
    ecrire(racine, "prestashop/commande", SKILL + "\n## Déclencheurs bis\nautre\n")
    ctx.appels.clear()
    pont._dernier_catalogue = 0.0
    pont._ronde_catalogue(force=True)
    assert "sections" in ctx.appels
    assert svc.catalog_.stats()["a_indexer"] == 0


def test_la_cadence_empeche_deux_rondes_de_suite(ronde):
    svc, ctx, _, _ = ronde
    pont._ronde_catalogue(force=True)
    ctx.appels.clear()
    pont._ronde_catalogue(force=False)
    assert ctx.appels == [], "une ronde par heure en regime normal"


def test_le_besoin_catalogue_vide_declenche_une_ronde(ronde):
    svc, ctx, _, _ = ronde
    resultat = svc.observe("s", "une reponse quelconque")
    assert resultat["besoins"] == ["catalogue_vide"]
    pont._suite_observe({"ok": True, "result": resultat})
    assert svc.catalog_.stats()["skills"] == 2, "GK reclame, le pont repond"


def test_une_ronde_sans_contexte_ne_leve_pas(monkeypatch):
    monkeypatch.setattr(pont, "_ctx", None)
    pont._ronde_catalogue(force=True)  # ne doit rien faire, ni lever


def test_un_refus_de_la_liste_blanche_est_journalise(ronde, monkeypatch):
    svc, ctx, _, tmp = ronde

    def refuse(*a, **k):
        raise PermissionError("gk absent de mcp_allowlist")

    monkeypatch.setattr(ctx, "call_mcp", refuse)
    pont._ronde_catalogue(force=True)
    lignes = [json.loads(l) for l in open(tmp / "journal.jsonl", encoding="utf-8")]
    resume = [l for l in lignes if l.get("event") == "ronde_catalogue"][-1]
    assert "refuse" in resume["erreur"]
