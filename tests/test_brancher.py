"""Teste le script d'insertion dans le config.yaml de l'agent.

Il edite un fichier de production de plusieurs centaines de lignes. Ce qui est
verifie ici est donc moins qu'il fonctionne que qu'il ne detruit rien : les
voisins, les commentaires, l'ordre, et le retour a l'etat initial si le YAML ne
se relit pas.

La structure de reference reproduit celle relevee le 2026-10-22 dans le
`config.yaml` de l'agent, hook `skills_gate.py` compris.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RACINE = Path(__file__).resolve().parents[1]

STRUCTURE_REELLE = """# commentaire a preserver
command_allowlist:
  - ls
plugins:
  enabled:
    - gm-skill-dup-guard
    - gm-destructive-guard
    - gm-discord-cards
    - gm-routing
  disabled:
    - gm-provenance
    - gm-routing-socle-test
  entries:
    gm-destructive-guard:
      allow_tool_override: false
    gm-routing:
      allow_tool_override: false
hooks:
  pre_llm_call:
    - command: /opt/data/.venv/bin/python3 /opt/data/hooks/skills_gate.py
      timeout: 5
mcp_servers:
  datagouv:
    url: https://mcp.data.gouv.fr/mcp
  openseo:
    url: https://app.openseo.so/mcp
    headers:
      Authorization: Bearer ${MCP_OPENSEO_API_KEY}
fin: oui
"""


@pytest.fixture
def brancher(tmp_path):
    """Charge le script en le pointant sur un faux config.yaml."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(STRUCTURE_REELLE, encoding="utf-8")
    src = (RACINE / "deploy" / "brancher-gk.py").read_text(encoding="utf-8")
    src = src.replace('Path("/opt/data/config.yaml")', f'Path("{cfg}")')
    chemin = tmp_path / "b.py"
    chemin.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("brancher", chemin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, cfg


def charge(cfg):
    return yaml.safe_load(cfg.read_text(encoding="utf-8"))


def test_les_trois_insertions(brancher):
    mod, cfg = brancher
    assert mod.main() == 0
    d = charge(cfg)
    assert d["mcp_servers"]["gk"]["url"] == "http://127.0.0.1:8765/mcp/bridge"
    assert d["mcp_servers"]["gk"]["headers"]["Authorization"] == "Bearer ${GK_BRIDGE_TOKEN}"
    assert "gk-bridge" in d["plugins"]["enabled"]
    assert d["plugins"]["entries"]["gk-bridge"]["mcp_allowlist"] == ["gk"]


def test_le_jeton_n_apparait_jamais(brancher):
    """Seule la reference est ecrite : le secret reste dans le .env."""
    mod, cfg = brancher
    mod.main()
    assert "${GK_BRIDGE_TOKEN}" in cfg.read_text(encoding="utf-8")


def test_les_voisins_sont_intacts(brancher):
    mod, cfg = brancher
    mod.main()
    d = charge(cfg)
    assert d["mcp_servers"]["openseo"]["headers"]["Authorization"] == "Bearer ${MCP_OPENSEO_API_KEY}"
    assert d["plugins"]["disabled"] == ["gm-provenance", "gm-routing-socle-test"]
    assert len(d["plugins"]["enabled"]) == 5
    assert d["plugins"]["entries"]["gm-routing"] == {"allow_tool_override": False}
    # Le hook existant ne doit pas bouger : c'est la phase 3 en production
    assert "skills_gate.py" in d["hooks"]["pre_llm_call"][0]["command"]
    assert d["hooks"]["pre_llm_call"][0]["timeout"] == 5


def test_commentaires_et_ordre_preserves(brancher):
    """Preuve qu'on insere au lieu de reecrire via l'analyseur, lequel
    detruirait commentaires et mise en forme d'un fichier de production."""
    mod, cfg = brancher
    mod.main()
    texte = cfg.read_text(encoding="utf-8")
    assert texte.startswith("# commentaire a preserver")
    assert texte.rstrip().endswith("fin: oui")


def test_idempotent(brancher):
    mod, cfg = brancher
    assert mod.main() == 0
    assert mod.main() == 0
    d = charge(cfg)
    assert d["plugins"]["enabled"].count("gk-bridge") == 1
    assert len(d["mcp_servers"]) == 3


def test_restaure_si_le_yaml_casse(brancher):
    mod, cfg = brancher
    cfg.write_text(
        STRUCTURE_REELLE.replace("mcp_servers:", "mcp_servers:\n  casse: [non ferme"),
        encoding="utf-8",
    )
    avant = cfg.read_text(encoding="utf-8")
    assert mod.main() == 1
    assert cfg.read_text(encoding="utf-8") == avant


@pytest.mark.parametrize("ancre", ["plugins:", "mcp_servers:", "  enabled:", "  entries:"])
def test_refuse_si_une_ancre_manque(brancher, ancre):
    """Mieux vaut ne rien faire que deviner ou inserer."""
    mod, cfg = brancher
    cfg.write_text(STRUCTURE_REELLE.replace(ancre + "\n", ""), encoding="utf-8")
    avant = cfg.read_text(encoding="utf-8")
    assert mod.main() == 1
    assert cfg.read_text(encoding="utf-8") == avant
