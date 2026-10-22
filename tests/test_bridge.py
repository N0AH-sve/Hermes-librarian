"""Tests du pont, contre un faux Hermes.

Le pont est la piece la plus risquee : il tourne dans l'agent de production. Ce
qui est verifie ici est exactement ce qui ne doit jamais arriver — une exception
qui remonte, un retour non nul sur `transform_llm_output`, ou une attente sur le
chemin de la reponse.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


def charger(tmp_path, monkeypatch):
    monkeypatch.setenv("GK_BRIDGE_LOG", str(tmp_path / "bridge.jsonl"))
    spec = importlib.util.spec_from_file_location(
        "gkbridge", RACINE / "hermes-plugin" / "gk-bridge" / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FauxCtx:
    """Reproduit la signature reelle relevee dans hermes_cli/plugins.py."""

    def __init__(self, leve=None):
        self.appels = []
        self.leve = leve

    def call_mcp(self, server: str, tool: str, arguments=None, timeout: float = 30):
        if self.leve:
            raise self.leve
        self.appels.append((server, tool, arguments, timeout))
        return {"content": [{"type": "text", "text": "{}"}]}


def journal(mod):
    p = Path(mod.JOURNAL)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").strip().splitlines() if l]


def attendre(ctx, n=1, limite=2.0):
    """Les appels partent sur un fil : on laisse le temps au thread."""
    debut = time.time()
    while len(ctx.appels) < n and time.time() - debut < limite:
        time.sleep(0.01)
    return ctx.appels


# -- la garantie centrale --------------------------------------------------


@pytest.mark.parametrize("cle", ["text", "output", "content", "response", "message"])
def test_renvoie_toujours_none_quelle_que_soit_la_cle(tmp_path, monkeypatch, cle):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    assert mod.on_transform_llm_output(ctx=ctx, session_id="s", **{cle: "Tu as raison."}) is None


def test_la_reponse_est_transmise_a_observe(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    mod.on_transform_llm_output(ctx=ctx, session_id="sess-7", text="Tu as raison.")
    appels = attendre(ctx)
    assert len(appels) == 1
    serveur, outil, args, timeout = appels[0]
    assert (serveur, outil) == ("gk", "observe")
    assert args["reply"] == "Tu as raison." and args["session_id"] == "sess-7"
    assert timeout == 1.0, "plancher impose par Hermes"


def test_ctx_trouve_ou_qu_il_arrive(tmp_path, monkeypatch):
    """Par ou arrive le PluginContext n'est pas connu : il est reconnu a ce
    qu'il sait faire, pas a son nom."""
    for nom in ("ctx", "context", "plugin_ctx", "api"):
        mod = charger(tmp_path, monkeypatch)
        ctx = FauxCtx()
        mod.on_transform_llm_output(text="Tu as raison.", **{nom: ctx})
        assert attendre(ctx), f"non trouve sous {nom!r}"
        assert any(e["event"] == "ctx_trouve" for e in journal(mod))


def test_ctx_passe_a_l_initialisation(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    mod.register(ctx)
    mod.on_transform_llm_output(text="Tu as raison.")  # sans ctx dans les kwargs
    assert attendre(ctx)


def test_sans_ctx_le_pont_se_tait(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    assert mod.on_transform_llm_output(text="Tu as raison.") is None
    assert mod.on_skill_lifecycle(skill_name="x") is None


# -- rien ne remonte jamais ------------------------------------------------


def test_une_permission_refusee_ne_remonte_pas(tmp_path, monkeypatch):
    """Defaut-refus de Hermes quand mcp_allowlist manque."""
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx(leve=PermissionError("pas dans mcp_allowlist"))
    assert mod.on_transform_llm_output(ctx=ctx, text="Tu as raison.") is None
    time.sleep(0.15)
    assert any(e["event"] == "refuse" for e in journal(mod))


def test_un_serveur_en_panne_ne_remonte_pas(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx(leve=RuntimeError("connexion refusee"))
    assert mod.on_transform_llm_output(ctx=ctx, text="Tu as raison.") is None
    time.sleep(0.15)
    assert any(e["event"] == "echec" for e in journal(mod))


@pytest.mark.parametrize("kw", [{}, {"text": None}, {"text": 12}, {"session_id": object()}])
def test_des_kwargs_absurdes_ne_remontent_pas(tmp_path, monkeypatch, kw):
    mod = charger(tmp_path, monkeypatch)
    assert mod.on_transform_llm_output(ctx=FauxCtx(), **kw) is None
    assert mod.on_skill_lifecycle(ctx=FauxCtx(), **kw) is None


def test_un_ctx_hostile_ne_remonte_pas(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)

    class Hostile:
        def __getattr__(self, n):
            raise RuntimeError("boom")

    assert mod.register(Hostile()) is None
    assert mod.on_transform_llm_output(text="Tu as raison.") is None


def test_journal_illisible_ne_remonte_pas(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    mod.JOURNAL = "/un/chemin/qui/n/existe/pas/x.jsonl"
    assert mod.on_transform_llm_output(ctx=FauxCtx(), text="Tu as raison.") is None


# -- le releve des inconnues ----------------------------------------------


def test_le_premier_appel_consigne_la_forme_reelle(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    mod.on_transform_llm_output(ctx=FauxCtx(), session_id="s", text="bonjour", model="x")
    premiers = [e for e in journal(mod) if e["event"] == "premier_appel"]
    assert len(premiers) == 1
    assert set(premiers[0]["cles_kwargs"]) == {"ctx", "session_id", "text", "model"}
    # Une seule fois : pas de journal qui gonfle a chaque tour
    mod.on_transform_llm_output(ctx=FauxCtx(), text="autre")
    assert len([e for e in journal(mod) if e["event"] == "premier_appel"]) == 1


def test_reponse_introuvable_est_signalee(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    mod.on_transform_llm_output(ctx=FauxCtx(), session_id="s", un_nom_inattendu="Tu as raison.")
    assert any(e["event"] == "reponse_introuvable" for e in journal(mod))


# -- cycle de vie des skills ----------------------------------------------


def test_skill_lifecycle_transmet_les_champs_reels(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    mod.on_skill_lifecycle(
        ctx=ctx, skill_name="perf-audit", action="use",
        session_id="s", task_id="t", turn_id="T7", use_count=14, provenance="local",
    )
    args = attendre(ctx)[0][2]
    assert args == {
        "skill_name": "perf-audit", "action": "use",
        "session_id": "s", "task_id": "t", "turn_id": "T7", "use_count": 14,
    }


def test_le_hook_d_entree_n_injecte_jamais_rien(tmp_path, monkeypatch):
    """`pre_llm_call` est branche depuis le 2026-10-22, en observation seule.

    C'est le hook par lequel on INJECTERAIT un brief. Il n'en est utilise que la
    moitie, et cette moitie doit etre verrouillee : renvoyer autre chose que
    None modifierait le chemin d'entree de l'agent. Verifie sur une entree
    normale, sur une entree absurde, et quand GK est injoignable.
    """
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    mod.register(ctx)

    assert mod.on_pre_llm_call(session_id="s", user_message="une demande") is None
    assert mod.on_pre_llm_call() is None, "sans aucun kwarg"
    assert mod.on_pre_llm_call(user_message=None) is None
    assert mod.on_pre_llm_call(user_message=12) is None
    assert mod.on_pre_llm_call(user_message="   ") is None, "une demande vide"

    casse = charger(tmp_path, monkeypatch)
    casse.register(FauxCtx(leve=RuntimeError("gk est tombe")))
    assert casse.on_pre_llm_call(session_id="s", user_message="x") is None


def test_les_trois_hooks_renvoient_toujours_none(tmp_path, monkeypatch):
    """La propriete qui rend le pont inoffensif, tenue par un test plutot que
    par la discipline."""
    mod = charger(tmp_path, monkeypatch)
    mod.register(FauxCtx())
    assert mod.on_transform_llm_output(session_id="s", response_text="une reponse") is None
    assert mod.on_skill_lifecycle(skill_name="un-skill", session_id="s") is None
    assert mod.on_pre_llm_call(session_id="s", user_message="une demande") is None


def test_la_demande_est_transmise_a_gk(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    mod.register(ctx)
    mod.on_pre_llm_call(
        session_id="s7", user_message="pourquoi on a paye 40 de port chez Adagio",
        is_first_turn=True, platform="discord",
    )
    appels = attendre(ctx, 1)
    serveur, outil, args, _ = appels[0]
    assert (serveur, outil) == ("gk", "request")
    assert args["text"] == "pourquoi on a paye 40 de port chez Adagio"
    assert args["session_id"] == "s7"
    assert args["is_first_turn"] is True
    assert args["platform"] == "discord"


def test_un_nom_de_champ_inconnu_est_journalise(tmp_path, monkeypatch):
    """La lecon de `response_text` : ne pas supposer le nom du champ, et dire
    quand aucun candidat ne marche."""
    mod = charger(tmp_path, monkeypatch)
    mod.register(FauxCtx())
    assert mod.on_pre_llm_call(session_id="s", une_cle_inattendue="x") is None
    evenements = [l["event"] for l in journal(mod)]
    assert "demande_introuvable" in evenements


def test_le_manifeste_declare_le_hook_d_entree(tmp_path, monkeypatch):
    """Il doit y etre, et le code doit le cabler : le manifeste declare, il ne
    cable pas -- `gm-routing` en declare deux et en enregistre cinq."""
    mod = charger(tmp_path, monkeypatch)
    assert "pre_llm_call" in [h for h, _ in mod.HOOKS]
    lignes = (
        (RACINE / "hermes-plugin" / "gk-bridge" / "plugin.yaml")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    declares = [l.strip()[2:].strip() for l in lignes if l.startswith("  - ")]
    assert declares == ["transform_llm_output", "on_skill_lifecycle", "pre_llm_call"]


# -- enregistrement des hooks ---------------------------------------------


def test_register_enregistre_vraiment_les_hooks(tmp_path, monkeypatch):
    """Le defaut du premier essai : le ctx etait recu, jamais utilise pour
    brancher quoi que ce soit. PluginContext se decrit comme la facade « so they
    can register tools and hooks » — il faut donc l'appeler."""
    mod = charger(tmp_path, monkeypatch)

    class CtxAvecRegister(FauxCtx):
        def __init__(self):
            super().__init__()
            self.hooks = []

        def register_hook(self, nom, fn):
            self.hooks.append((nom, fn))

    ctx = CtxAvecRegister()
    mod.register(ctx)
    noms = [n for n, _ in ctx.hooks]
    assert "transform_llm_output" in noms
    assert "on_skill_lifecycle" in noms
    # Et les fonctions branchees doivent etre les bonnes
    pour_sortie = dict(ctx.hooks)["transform_llm_output"]
    assert pour_sortie(ctx=ctx, text="Tu as raison.") is None
    assert attendre(ctx)


def test_sans_register_hook_le_pont_le_dit(tmp_path, monkeypatch):
    """FauxCtx n'a pas register_hook : le pont doit le signaler, pas se taire."""
    mod = charger(tmp_path, monkeypatch)
    mod.register(FauxCtx())
    assert any(
        e["event"] == "charge" and e.get("hooks") == "AUCUN" for e in journal(mod)
    )


def test_un_enregistrement_qui_leve_ne_remonte_pas(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)

    class Hostile(FauxCtx):
        def register_hook(self, nom, fn):
            raise RuntimeError("refuse")

    assert mod.register(Hostile()) is None
    assert any(e["event"] == "enregistrement_refuse" for e in journal(mod))


def test_un_seul_point_d_entree(tmp_path, monkeypatch):
    """`register(ctx)` est verifie dans la source et dans gm-routing. Les alias
    de tatonnement sont retires : ils n'ont plus lieu d'etre."""
    mod = charger(tmp_path, monkeypatch)
    assert callable(mod.register)
    for ancien in ("setup", "init", "activate", "on_load"):
        assert not hasattr(mod, ancien), f"{ancien} devrait avoir disparu"


# -- la forme reelle des kwargs, relevee en production --------------------


def test_la_reponse_arrive_sous_response_text(tmp_path, monkeypatch):
    """Relevé le 2026-10-22 : les kwargs de `transform_llm_output` sont
    {model, platform, response_text, session_id, telemetry_schema_version,
    turn_id}. `response_text` n'etait dans aucune des sept hypotheses."""
    mod = charger(tmp_path, monkeypatch)
    ctx = FauxCtx()
    assert mod.on_transform_llm_output(
        ctx=ctx, session_id="s", turn_id="T1", response_text="Tu as raison.",
        model="x", platform="cli", telemetry_schema_version=3,
    ) is None
    args = attendre(ctx)[0][2]
    assert args["reply"] == "Tu as raison."
    assert args["turn_id"] == "T1", "turn_id doit etre transmis pour l'attribution"


def test_les_kwargs_reels_ne_declenchent_plus_reponse_introuvable(tmp_path, monkeypatch):
    mod = charger(tmp_path, monkeypatch)
    mod.on_transform_llm_output(
        ctx=FauxCtx(), session_id="s", turn_id="T1", response_text="bonjour",
        model="x", platform="cli", telemetry_schema_version=3,
    )
    assert not any(e["event"] == "reponse_introuvable" for e in journal(mod))
