"""Tests du plugin de l'optimiseur, contre un faux Hermes.

Ce plugin est la piece la plus risquee du projet, plus encore que gk-bridge :
il tourne dans l'agent de production **et sur le chemin d'entree du tour**. Ce
qui est verifie ici est donc exactement ce qui ne doit jamais arriver :

* une exception qui remonte ;
* le message de l'utilisateur reecrit au lieu d'etre complete ;
* une attente non bornee quand GK est lent ou mort ;
* un appel par message quand GK est tombe.

Rien n'y teste la qualite des corrections : elle est dans `test_optimizer.py`,
cote serveur, ou elle se corrige sans redeployer chez l'agent.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
BLOC = "[GK] STOP — n'execute aucune action.\n     Question a poser : prod ou staging ?"


def charger(tmp_path, monkeypatch):
    """Un module neuf a chaque test : le plugin garde son contexte et son
    coupe-circuit dans des globales, comme gk-bridge."""
    monkeypatch.setenv("GK_OPTIMIZER_LOG", str(tmp_path / "optimizer.jsonl"))
    spec = importlib.util.spec_from_file_location(
        "gkoptimizer", RACINE / "hermes-plugin" / "gk-optimizer" / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FauxCtx:
    """Reproduit la signature relevee dans hermes_cli/plugins.py, et rend la
    reponse dans l'enveloppe MCP, qui est la forme la plus probable."""

    def __init__(self, bloc: str = BLOC, leve=None, lenteur: float = 0.0, section=True):
        self.appels: list[tuple] = []
        self.sections: list[str] = []
        self.bloc = bloc
        self.leve = leve
        self.lenteur = lenteur
        self._section = section

    def call_mcp(self, server: str, tool: str, arguments=None, timeout: float = 30):
        self.appels.append((server, tool, arguments, timeout))
        if self.lenteur:
            time.sleep(self.lenteur)
        if self.leve:
            raise self.leve
        charge = {"v": 1, "block": self.bloc, "prompt_section": "SECTION SERVIE PAR GK"}
        return {"content": [{"type": "text", "text": json.dumps(charge)}]}

    def register_system_prompt_section(self, texte: str):
        if not self._section:
            raise RuntimeError("point d'accroche absent")
        self.sections.append(texte)


def journal(mod) -> list[dict]:
    p = Path(mod.JOURNAL)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").strip().splitlines() if l]


# -- la garantie principale ------------------------------------------------


def test_ajoute_sans_jamais_reecrire(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    message = "deploie le site stp"
    retour = mod.on_pre_llm_call(ctx=FauxCtx(), session_id="s1", message=message)
    assert retour.startswith(message), "le message de l'utilisateur doit rester un prefixe"
    assert retour.endswith(BLOC)


def test_le_message_part_bien_au_serveur(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    mod.on_pre_llm_call(ctx=ctx, session_id="sess-7", message="deploie")
    serveur, outil, arguments, timeout = ctx.appels[0]
    assert (serveur, outil) == ("gk", "optimize")
    assert arguments == {"session_id": "sess-7", "text": "deploie"}
    assert timeout == 1.0, "le plancher impose par Hermes"


@pytest.mark.parametrize(
    "cle", ["message", "text", "prompt", "user_message", "content", "input", "query", "task"]
)
def test_trouve_le_message_sous_tous_les_noms_plausibles(tmp_path, monkeypatch, cle):
    mod = charger(tmp_path, monkeypatch)
    retour = mod.on_pre_llm_call(ctx=FauxCtx(), **{cle: "une demande"})
    assert retour == "une demande\n\n" + BLOC


def test_un_message_dans_une_liste_est_modifie_sur_place(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    messages = [
        {"role": "user", "content": "premier"},
        {"role": "assistant", "content": "reponse"},
        {"role": "user", "content": "deploie le site"},
    ]
    retour = mod.on_pre_llm_call(ctx=FauxCtx(), messages=messages)
    assert retour is None, "une mutation ne doit pas aussi renvoyer, sinon double ajout"
    assert messages[2]["content"] == "deploie le site\n\n" + BLOC
    assert messages[0]["content"] == "premier", "seul le dernier message utilisateur est touche"
    assert [e["voie"] for e in journal(mod) if e["event"] == "ajoute"] == ["mutation"]


def test_un_contenu_en_blocs_est_modifie_sur_place(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    messages = [{"role": "user", "content": [{"type": "text", "text": "deploie"}]}]
    assert mod.on_pre_llm_call(ctx=FauxCtx(), messages=messages) is None
    assert messages[0]["content"][0]["text"] == "deploie\n\n" + BLOC


# -- fail open : tout ce qui doit rendre un tour inchange ------------------


@pytest.mark.parametrize(
    "ctx,raison",
    [
        (None, "aucun contexte"),
        (FauxCtx(bloc=""), "bloc vide"),
        (FauxCtx(leve=PermissionError("pas dans mcp_allowlist")), "refus de l'allowlist"),
        (FauxCtx(leve=RuntimeError("boom")), "erreur du serveur"),
        (FauxCtx(leve=TimeoutError()), "delai du serveur"),
    ],
)
def test_rend_none_plutot_que_d_echouer(tmp_path, monkeypatch, ctx, raison):
    mod = charger(tmp_path, monkeypatch)
    kwargs = {"message": "une demande"}
    if ctx is not None:
        kwargs["ctx"] = ctx
    assert mod.on_pre_llm_call(**kwargs) is None, raison


def test_un_message_introuvable_est_journalise_pas_fatal(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    assert mod.on_pre_llm_call(ctx=FauxCtx(), un_nom_inattendu="une demande") is None
    assert any(e["event"] == "message_introuvable" for e in journal(mod))


def test_un_message_vide_ne_declenche_aucun_appel(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    assert mod.on_pre_llm_call(ctx=ctx, message="   ") is None
    assert ctx.appels == []


@pytest.mark.parametrize("retour", [None, 42, "pas du json", {"inattendu": True}, [1, 2]])
def test_un_retour_de_call_mcp_inconnu_rend_un_tour_inchange(tmp_path, monkeypatch, retour):
    mod = charger(tmp_path, monkeypatch)

    class Etrange:
        def call_mcp(self, *a, **k):
            return retour

    assert mod.on_pre_llm_call(ctx=Etrange(), message="une demande") is None


def test_meme_une_baseexception_du_serveur_ne_salit_rien(tmp_path, monkeypatch):
    """Elle est attrapee parce qu'on est sur un fil : ce qui s'en echappe ne
    peut etre rattrape par personne et finit en trace dans la sortie de
    l'agent."""
    mod = charger(tmp_path, monkeypatch)

    class Pire:
        def call_mcp(self, *a, **k):
            raise BaseException("meme pas une Exception")  # noqa: TRY002

    assert mod.on_pre_llm_call(ctx=Pire(), message="une demande") is None
    assert any(e["event"] == "echec" for e in journal(mod))


def test_une_charge_json_directe_marche_aussi(tmp_path, monkeypatch):
    """La forme de retour de call_mcp n'est pas confirmee : un dictionnaire nu
    doit marcher autant que l'enveloppe MCP."""
    mod = charger(tmp_path, monkeypatch)

    class Direct:
        def call_mcp(self, *a, **k):
            return {"v": 1, "block": BLOC}

    assert mod.on_pre_llm_call(ctx=Direct(), message="une demande").endswith(BLOC)


# -- latence et coupe-circuit ----------------------------------------------


def test_n_attend_jamais_plus_que_le_plafond(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    depart = time.time()
    retour = mod.on_pre_llm_call(ctx=FauxCtx(lenteur=5.0), message="une demande")
    ecoule = time.time() - depart
    assert retour is None
    assert ecoule < mod.ATTENTE_MAX + 0.5, f"{ecoule:.2f}s : le chemin d'entree doit etre borne"


def test_le_coupe_circuit_s_ouvre_et_arrete_les_appels(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx(leve=RuntimeError("serveur arrete"))
    for _ in range(mod.ECHECS_AVANT_COUPURE + 4):
        assert mod.on_pre_llm_call(ctx=ctx, message="une demande") is None
    assert len(ctx.appels) == mod.ECHECS_AVANT_COUPURE, (
        "un GK tombe ne doit pas coûter un appel par message"
    )
    assert any(e.get("coupe_circuit_ouvert") for e in journal(mod))


def test_le_coupe_circuit_se_referme(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx(leve=RuntimeError("boom"))
    for _ in range(mod.ECHECS_AVANT_COUPURE):
        mod.on_pre_llm_call(ctx=ctx, message="une demande")
    mod._rouvre_a = 0.0  # la coupure expire
    ctx.leve = None
    assert mod.on_pre_llm_call(ctx=ctx, message="une demande") is not None


# -- la section de prompt --------------------------------------------------


def test_la_section_vient_du_serveur_et_une_seule_fois(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    for _ in range(3):
        mod.on_pre_llm_call(ctx=ctx, message="une demande")
    assert ctx.sections == ["SECTION SERVIE PAR GK"]


def test_un_point_d_accroche_absent_n_est_pas_une_panne(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)

    class SansAccroche:
        def call_mcp(self, *a, **k):
            return {"block": BLOC}

    assert mod.on_pre_llm_call(ctx=SansAccroche(), message="une demande").endswith(BLOC)
    assert any(e["event"] == "section_impossible" for e in journal(mod))


def test_une_section_qui_explose_n_empeche_pas_le_bloc(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx(section=False)
    assert mod.on_pre_llm_call(ctx=ctx, message="une demande").endswith(BLOC)
    assert any(e["event"] == "section_echouee" for e in journal(mod))


# -- decouverte de l'API et manifeste --------------------------------------


def test_le_premier_appel_consigne_la_forme_reelle_de_l_api(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    mod.on_pre_llm_call(ctx=FauxCtx(), session_id="s", message="une demande", model="x")
    premier = [e for e in journal(mod) if e["event"] == "premier_appel"]
    assert len(premier) == 1
    assert premier[0]["cles_kwargs"] == ["ctx", "message", "model", "session_id"]
    mod.on_pre_llm_call(ctx=FauxCtx(), message="autre")
    assert len([e for e in journal(mod) if e["event"] == "premier_appel"]) == 1


@pytest.mark.parametrize("nom", ["ctx", "context", "plugin_context", "gk"])
def test_le_contexte_est_reconnu_sous_n_importe_quel_nom(tmp_path, monkeypatch, nom):
    mod = charger(tmp_path, monkeypatch)
    assert mod.on_pre_llm_call(**{nom: FauxCtx(), "message": "une demande"}) is not None


def test_register_ne_leve_jamais(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    assert mod.register() is None
    assert mod.register(ctx=FauxCtx()) is None
    assert mod.register(object()) is None
    assert mod.setup is mod.register and mod.on_load is mod.register


def test_le_manifeste_declare_le_seul_hook_present(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    manifeste = (RACINE / "hermes-plugin" / "gk-optimizer" / "plugin.yaml").read_text(
        encoding="utf-8"
    )
    assert "pre_llm_call" in manifeste
    assert hasattr(mod, "on_pre_llm_call")
    # L'optimiseur n'observe pas : c'est le travail de gk-bridge, et deux
    # plugins qui observent le meme tour compteraient deux fois.
    assert not hasattr(mod, "on_transform_llm_output")
    assert "transform_llm_output" not in manifeste


def test_n_utilise_que_la_bibliotheque_standard(tmp_path, monkeypatch):
    """Contrainte de l'etape 6, pas une preference : on ne peut rien installer
    dans l'image de Hermes."""
    source = (RACINE / "hermes-plugin" / "gk-optimizer" / "__init__.py").read_text(
        encoding="utf-8"
    )
    imports = {
        l.split()[1].split(".")[0]
        for l in source.splitlines()
        if l.startswith(("import ", "from ")) and "__future__" not in l
    }
    assert imports <= {"json", "os", "threading", "time"}, imports
