"""gk-probe : sonde jetable pour relever l'API de plugin de Hermes.

Elle n'apporte aucune fonctionnalite. Elle existe pour repondre a une question
qui bloque l'implementation de GreenKnowledge : quelle est la forme exacte de ce
que renvoie `ctx.call_mcp` sur CETTE version de Hermes.

Le prototype decrit dans le document d'architecture accepte "trois formats
plausibles" faute de le savoir. Mieux vaut mesurer.

Trois garanties, dans cet ordre d'importance :

1. Elle ne leve jamais d'exception, ni a l'import ni dans le hook. Tout est
   enveloppe. On ne sait pas encore si un plugin qui plante empeche gb de
   demarrer, donc on se comporte comme si oui.
2. Elle n'injecte rien dans le contexte de gb. Le hook renvoie toujours une
   chaine vide, donc le comportement de l'agent est inchange.
3. Elle n'agit qu'une fois, puis devient inerte. Pas de latence ajoutee aux
   tours suivants.

Elle ecrit son releve dans PROBE_LOG, en JSON, une ligne par execution.

A SUPPRIMER des que le releve a ete lu.
"""

import json
import os
import time

PROBE_LOG = os.environ.get("GK_PROBE_LOG", "/opt/data/gk-probe.jsonl")

# Nom d'un serveur MCP deja declare dans la config de Hermes. Laisser vide pour
# se limiter a l'introspection, qui ne touche a rien. Renseigner pour tenter un
# vrai appel et relever la forme du retour.
PROBE_MCP_SERVER = os.environ.get("GK_PROBE_MCP_SERVER", "")
PROBE_MCP_TOOL = os.environ.get("GK_PROBE_MCP_TOOL", "")

_done = False


def _write(record):
    """Ecrit une ligne de releve. Avale toute erreur : un echec d'ecriture ne
    doit pas remonter dans gb."""
    try:
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(PROBE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=repr) + "\n")
    except Exception:
        pass


def _describe(obj, name):
    """Decrit un objet sans l'appeler : type, signature, docstring."""
    out = {"name": name, "type": type(obj).__name__}
    try:
        out["repr"] = repr(obj)[:500]
    except Exception as exc:
        out["repr_error"] = repr(exc)
    try:
        import inspect

        if callable(obj):
            out["signature"] = str(inspect.signature(obj))
            doc = inspect.getdoc(obj)
            if doc:
                out["doc"] = doc[:1500]
            try:
                out["annotations"] = {
                    k: str(v) for k, v in getattr(obj, "__annotations__", {}).items()
                }
            except Exception:
                pass
    except Exception as exc:
        out["inspect_error"] = repr(exc)
    return out


def _introspect(ctx):
    """Releve l'API offerte par ctx, sans rien appeler."""
    api = {}
    try:
        names = [n for n in dir(ctx) if not n.startswith("_")]
    except Exception as exc:
        return {"dir_error": repr(exc)}
    api["members"] = names
    # Les points d'API dont depend GreenKnowledge, decrits un par un.
    for name in (
        "call_mcp",
        "register_hook",
        "register_system_prompt_section",
        "list_mcp_servers",
        "logger",
        "config",
    ):
        try:
            if hasattr(ctx, name):
                api[name] = _describe(getattr(ctx, name), name)
            else:
                api[name] = {"absent": True}
        except Exception as exc:
            api[name] = {"error": repr(exc)}
    return api


def _try_call_mcp(ctx):
    """Tente un vrai appel, uniquement si un serveur a ete designe.

    C'est la seule partie qui sort du plugin. Elle ne tourne pas par defaut.
    """
    if not (PROBE_MCP_SERVER and PROBE_MCP_TOOL):
        return {"skipped": "aucun serveur designe"}
    try:
        result = ctx.call_mcp(PROBE_MCP_SERVER, PROBE_MCP_TOOL, {})
    except Exception as exc:
        return {"raised": repr(exc), "type": type(exc).__name__}
    out = {"type": type(result).__name__, "repr": repr(result)[:2000]}
    # La question qui compte : ou se trouve le texte utile dans ce retour.
    try:
        if isinstance(result, dict):
            out["keys"] = sorted(result.keys())
        else:
            out["attributes"] = [a for a in dir(result) if not a.startswith("_")]
        if hasattr(result, "__await__"):
            out["awaitable"] = True
    except Exception as exc:
        out["shape_error"] = repr(exc)
    return out


def _probe(ctx):
    global _done
    if _done:
        return ""
    _done = True
    record = {"event": "probe"}
    try:
        record["hermes_version"] = os.environ.get("HERMES_VERSION", "inconnu")
        record["hermes_home"] = os.environ.get("HERMES_HOME", "")
        record["home"] = os.environ.get("HOME", "")
        record["ctx_api"] = _introspect(ctx)
        record["call_mcp_result"] = _try_call_mcp(ctx)
    except Exception as exc:
        record["probe_error"] = repr(exc)
    _write(record)
    return ""


def _on_pre_llm_call(ctx, *args, **kwargs):
    """Hook. Renvoie toujours une chaine vide : rien n'est injecte."""
    try:
        return _probe(ctx)
    except Exception:
        return ""


def register(ctx, *args, **kwargs):
    """Point d'entree.

    Le nom exact attendu par Hermes fait partie de ce qu'on ignore, d'ou les
    alias en bas de fichier. Celui qui sera appele fera le travail, les autres
    resteront inertes.
    """
    try:
        ctx.register_hook("pre_llm_call", _on_pre_llm_call)
        _write({"event": "register", "ok": True})
    except Exception as exc:
        # Un echec d'enregistrement est une information, pas une erreur a
        # propager : on le note et on se tait.
        _write({"event": "register", "ok": False, "error": repr(exc)})
    return None


setup = register
init = register
activate = register
on_load = register
