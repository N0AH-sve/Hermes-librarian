"""Teste le script qui active la consultation `/gk`.

Il edite un fichier de production de plusieurs centaines de lignes, et une
erreur d'indentation YAML ne se voit qu'au redemarrage de l'agent -- donc quand
les collegues ne peuvent plus travailler. Ce qui est verifie ici est moins qu'il
fonctionne que **qu'il ne detruit rien** : les voisins, les commentaires,
l'ordre, l'entree du plugin, et le retour a l'etat initial au moindre doute.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RACINE = Path(__file__).resolve().parents[1]

# La structure relevee apres `brancher-gk.py`, commentaires compris.
APRES_BRANCHEMENT = """# commentaire a preserver
command_allowlist:
  - ls
plugins:
  enabled:
    - gm-discord-cards
    - gm-routing
    - gk-bridge
  disabled:
    - gm-provenance
  entries:
    gm-destructive-guard:
      allow_tool_override: false
    gk-bridge:
      allow_tool_override: false
      mcp_allowlist: ["gk"]
    gm-routing:
      allow_tool_override: false
hooks:
  pre_llm_call:
    - command: /opt/data/hooks/skills_gate.py
      timeout: 5
mcp_servers:
  gk:
    url: http://127.0.0.1:8765/mcp/bridge
    headers:
      Authorization: Bearer ${GK_BRIDGE_TOKEN}
fin: oui
"""


def charger(tmp_path, contenu=APRES_BRANCHEMENT):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(contenu, encoding="utf-8")
    src = (RACINE / "deploy" / "activer-consultation.py").read_text(encoding="utf-8")
    src = src.replace('Path("/opt/data/config.yaml")', f'Path("{cfg}")')
    chemin = tmp_path / "a.py"
    chemin.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("activer", chemin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, cfg


def charge(cfg):
    return yaml.safe_load(cfg.read_text(encoding="utf-8"))


def entree(cfg):
    return charge(cfg)["plugins"]["entries"]["gk-bridge"]


# -- le cas normal ---------------------------------------------------------


def test_la_variable_est_posee_dans_l_entree_du_pont(tmp_path):
    mod, cfg = charger(tmp_path)
    assert mod.main([]) == 0
    assert entree(cfg)["env"]["GK_CONSULTATION"] == "1"


def test_la_valeur_est_une_chaine_et_non_un_entier(tmp_path):
    """Une variable d'environnement est une chaine. Un `1` nu serait relu comme
    un entier, et le pont compare a des chaines."""
    mod, cfg = charger(tmp_path)
    mod.main([])
    assert '"1"' in cfg.read_text(encoding="utf-8")
    assert isinstance(entree(cfg)["env"]["GK_CONSULTATION"], str)


def test_les_voisins_ne_bougent_pas(tmp_path):
    """L'insertion ne doit toucher que `gk-bridge` : les autres entrees
    autorisent d'autres plugins, et les perdre les couperait en silence."""
    mod, cfg = charger(tmp_path)
    avant = charge(cfg)
    mod.main([])
    apres = charge(cfg)
    for cle in ("command_allowlist", "hooks", "mcp_servers", "fin"):
        assert apres[cle] == avant[cle], cle
    assert apres["plugins"]["enabled"] == avant["plugins"]["enabled"]
    assert apres["plugins"]["disabled"] == avant["plugins"]["disabled"]
    for nom in ("gm-destructive-guard", "gm-routing"):
        assert apres["plugins"]["entries"][nom] == avant["plugins"]["entries"][nom]


def test_l_allowlist_est_preservee(tmp_path):
    """C'est elle qui autorise `call_mcp` : la perdre couperait le pont sans
    aucune erreur visible."""
    mod, cfg = charger(tmp_path)
    mod.main([])
    assert entree(cfg)["mcp_allowlist"] == ["gk"]
    assert entree(cfg)["allow_tool_override"] is False


def test_le_commentaire_de_tete_survit(tmp_path):
    """Une relecture YAML suivie d'une reecriture les detruirait tous."""
    mod, cfg = charger(tmp_path)
    mod.main([])
    assert cfg.read_text(encoding="utf-8").startswith("# commentaire a preserver")


def test_relancer_ne_duplique_rien(tmp_path):
    mod, cfg = charger(tmp_path)
    assert mod.main([]) == 0
    texte_un = cfg.read_text(encoding="utf-8")
    assert mod.main([]) == 0
    assert cfg.read_text(encoding="utf-8") == texte_un
    assert texte_un.count("GK_CONSULTATION") == 1


def test_une_sauvegarde_est_ecrite(tmp_path):
    mod, cfg = charger(tmp_path)
    mod.main([])
    sauvegardes = list(tmp_path.glob("config.yaml.bak-avant-consultation-*"))
    assert len(sauvegardes) == 1
    assert "GK_CONSULTATION" not in sauvegardes[0].read_text(encoding="utf-8")


# -- les formes deja presentes --------------------------------------------


def test_un_env_existant_est_complete_et_non_duplique(tmp_path):
    """Creer un second `env:` donnerait un YAML ou l'une des deux clefs est
    silencieusement perdue."""
    contenu = APRES_BRANCHEMENT.replace(
        '      mcp_allowlist: ["gk"]',
        '      mcp_allowlist: ["gk"]\n      env:\n        AUTRE: "garde moi"',
    )
    mod, cfg = charger(tmp_path, contenu)
    assert mod.main([]) == 0
    env = entree(cfg)["env"]
    assert env["GK_CONSULTATION"] == "1"
    assert env["AUTRE"] == "garde moi", "la variable voisine survit"
    assert cfg.read_text(encoding="utf-8").count("env:") == 1


def test_une_valeur_differente_est_corrigee(tmp_path):
    contenu = APRES_BRANCHEMENT.replace(
        '      mcp_allowlist: ["gk"]',
        '      mcp_allowlist: ["gk"]\n      env:\n        GK_CONSULTATION: "0"',
    )
    mod, cfg = charger(tmp_path, contenu)
    assert mod.main([]) == 0
    assert entree(cfg)["env"]["GK_CONSULTATION"] == "1"
    assert cfg.read_text(encoding="utf-8").count("GK_CONSULTATION") == 1


def test_un_env_en_ligne_est_refuse_sans_rien_ecrire(tmp_path):
    """Un `env: {...}` ne se complete pas par insertion de ligne. Refuser vaut
    mieux que produire un fichier que l'agent ne relira pas."""
    contenu = APRES_BRANCHEMENT.replace(
        '      mcp_allowlist: ["gk"]',
        '      mcp_allowlist: ["gk"]\n      env: {AUTRE: "x"}',
    )
    mod, cfg = charger(tmp_path, contenu)
    avant = cfg.read_text(encoding="utf-8")
    assert mod.main([]) == 1
    assert cfg.read_text(encoding="utf-8") == avant, "rien n'a ete ecrit"
    assert not list(tmp_path.glob("*.bak-avant-consultation-*"))


# -- les refus -------------------------------------------------------------


def test_sans_entree_gk_bridge_le_script_refuse(tmp_path):
    """Lancer celui-ci avant `brancher-gk.py` ne doit rien ecrire."""
    contenu = APRES_BRANCHEMENT.replace(
        "    gk-bridge:\n      allow_tool_override: false\n"
        '      mcp_allowlist: ["gk"]\n',
        "",
    )
    mod, cfg = charger(tmp_path, contenu)
    avant = cfg.read_text(encoding="utf-8")
    assert mod.main([]) == 1
    assert cfg.read_text(encoding="utf-8") == avant


def test_sans_section_plugins_le_script_refuse(tmp_path):
    mod, cfg = charger(tmp_path, "fin: oui\n")
    assert mod.main([]) == 1
    assert cfg.read_text(encoding="utf-8") == "fin: oui\n"


# -- la desactivation ------------------------------------------------------


def test_la_desactivation_retire_la_variable(tmp_path):
    mod, cfg = charger(tmp_path)
    assert mod.main([]) == 0
    assert mod.main(["--desactiver"]) == 0
    assert "GK_CONSULTATION" not in cfg.read_text(encoding="utf-8")
    # Et le reste de l'entree est intact.
    assert entree(cfg)["mcp_allowlist"] == ["gk"]


def test_la_desactivation_sans_rien_a_faire_ne_touche_pas_le_fichier(tmp_path):
    mod, cfg = charger(tmp_path)
    avant = cfg.read_text(encoding="utf-8")
    assert mod.main(["--desactiver"]) == 0
    assert cfg.read_text(encoding="utf-8") == avant
    assert not list(tmp_path.glob("*.bak-avant-consultation-*"))


def test_la_desactivation_garde_les_variables_voisines(tmp_path):
    contenu = APRES_BRANCHEMENT.replace(
        '      mcp_allowlist: ["gk"]',
        '      mcp_allowlist: ["gk"]\n      env:\n        AUTRE: "garde moi"',
    )
    mod, cfg = charger(tmp_path, contenu)
    mod.main([])
    assert mod.main(["--desactiver"]) == 0
    assert entree(cfg)["env"]["AUTRE"] == "garde moi"


# -- la restauration -------------------------------------------------------


def test_un_yaml_casse_est_restaure(tmp_path, monkeypatch):
    """La garantie qui compte : si quoi que ce soit cloche apres ecriture, le
    fichier revient a son etat initial."""
    mod, cfg = charger(tmp_path)
    avant = cfg.read_text(encoding="utf-8")

    def _casse(*a, **k):
        raise ValueError("YAML illisible")

    monkeypatch.setattr(mod.yaml if hasattr(mod, "yaml") else yaml, "safe_load", _casse)
    import yaml as vrai_yaml
    monkeypatch.setattr(vrai_yaml, "safe_load", _casse)
    assert mod.main([]) == 1
    assert cfg.read_text(encoding="utf-8") == avant


def test_une_allowlist_perdue_declenche_la_restauration(tmp_path, monkeypatch):
    """Simule le pire : une ecriture qui passe le YAML mais casse l'entree."""
    mod, cfg = charger(tmp_path)
    avant = cfg.read_text(encoding="utf-8")
    vrai = mod.CONFIG.write_text

    def _ecrire_casse(texte, **kw):
        return vrai(texte.replace('mcp_allowlist: ["gk"]', "mcp_allowlist: []"), **kw)

    monkeypatch.setattr(type(mod.CONFIG), "write_text", lambda self, t, **k: _ecrire_casse(t, **k))
    assert mod.main([]) == 1
    assert cfg.read_text(encoding="utf-8") == avant
