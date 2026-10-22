"""gk-bridge : le pont entre Hermes et GreenKnowledge.

Volontairement bete. Toute l'intelligence est dans GK, pour deux raisons tenues
de l'etape 5 :

* **GK doit pouvoir evoluer sans que ce fichier bouge.** Il vit dans le volume
  persistant de Hermes ; le modifier veut dire toucher a la production. Il ne
  comprend donc rien a ce qu'il transporte.
* **Il doit etre bon marche a reecrire.** L'API de plugin appartient a un projet
  tiers en developpement rapide. Cinquante lignes sans etat, bibliotheque
  standard seule, se reecrivent en une heure quand un point d'accroche est
  renomme. C'est la vraie assurance, pas la compatibilite.

PHASE 1 — il n'injecte RIEN. Aucun `pre_llm_call`, donc le chemin d'entree de
l'agent n'est pas touche. Il observe et il range, et c'est tout : la chaine de
mesure se deploie avant l'aide, pour avoir un point de comparaison et pour
valider la plomberie sur une charge qui ne peut rien casser.

Deux details de l'API de Hermes restent inconnus a l'ecriture, et ce fichier les
decouvre au lieu de les supposer : par ou arrive le `PluginContext`, et sous
quel nom la reponse de l'agent est passee a `transform_llm_output`. Les deux
sont cherches parmi les possibilites plausibles, et le premier appel journalise
ce qui a reellement marche. Le releve se lit dans
`/opt/data/gk-bridge.jsonl`.
"""

from __future__ import annotations

import json
import os
import threading
import time

JOURNAL = os.environ.get("GK_BRIDGE_LOG", "/opt/data/gk-bridge.jsonl")
SERVEUR_MCP = "gk"
TIMEOUT = 1.0  # plancher impose par Hermes : call_mcp borne a 1-600 s

# Noms sous lesquels la reponse de l'agent peut arriver. Le premier present
# gagne, et le releve dit lequel.
CLES_REPONSE = ("text", "output", "content", "response", "message", "final_response", "llm_output")

_ctx = None          # PluginContext, des qu'on l'a vu passer
_releve_fait = False
_lock = threading.Lock()


def _noter(record: dict) -> None:
    """Journalise. Avale toute erreur : un echec d'ecriture ne doit pas remonter
    dans un tour de l'agent."""
    try:
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=repr) + "\n")
    except Exception:
        pass


def _retenir_ctx(kwargs: dict):
    """Trouve le PluginContext, ou qu'il arrive.

    Il peut etre passe dans les kwargs du hook, ou avoir ete remis a une
    fonction d'initialisation. On le reconnait a ce qu'il sait faire plutot qu'a
    son nom ou son type.
    """
    global _ctx
    if _ctx is not None:
        return _ctx
    for cle, valeur in kwargs.items():
        if hasattr(valeur, "call_mcp"):
            _ctx = valeur
            _noter({"event": "ctx_trouve", "ou": f"kwargs[{cle!r}]"})
            return _ctx
    return None


def _reponse(kwargs: dict) -> tuple[str | None, str | None]:
    for cle in CLES_REPONSE:
        valeur = kwargs.get(cle)
        if isinstance(valeur, str) and valeur:
            return valeur, cle
    return None, None


def _releve_unique(hook: str, kwargs: dict) -> None:
    """Au premier appel seulement : consigne la forme reelle de l'API.

    Sans cela, les deux inconnues restantes se devinent. Avec, elles se lisent.
    """
    global _releve_fait
    with _lock:
        if _releve_fait:
            return
        _releve_fait = True
    _noter({
        "event": "premier_appel",
        "hook": hook,
        "cles_kwargs": sorted(kwargs),
        "ctx_present": _ctx is not None,
        "hermes_home": os.environ.get("HERMES_HOME", ""),
    })


def _appeler(outil: str, arguments: dict) -> None:
    """Envoie et oublie. Jamais d'attente sur le chemin d'une reponse.

    `transform_llm_output` est sur le trajet de la reponse a l'utilisateur :
    attendre GK y ajouterait sa latence pour rien, puisque nous n'utilisons pas
    le resultat.
    """
    ctx = _ctx
    if ctx is None:
        return

    def _tache():
        try:
            ctx.call_mcp(SERVEUR_MCP, outil, arguments, timeout=TIMEOUT)
        except PermissionError as exc:
            # Defaut-refus de Hermes : le serveur n'est pas dans
            # plugins.entries.gk-bridge.mcp_allowlist du config.yaml.
            _noter({"event": "refuse", "outil": outil, "erreur": repr(exc)})
        except Exception as exc:
            _noter({"event": "echec", "outil": outil, "erreur": repr(exc)})

    threading.Thread(target=_tache, name="gk-bridge", daemon=True).start()


# -- hooks ------------------------------------------------------------------


def on_transform_llm_output(**kwargs):
    """Observe la reponse de l'agent. RENVOIE TOUJOURS None.

    Ce hook attend une chaine de remplacement, et le premier retour non nul
    gagne. Renvoyer None est donc strictement sans effet : nous recevons le
    texte sans jamais rien modifier.

    C'est ce hook et non `post_llm_call`, dont la charge utile ne porte pas la
    reponse.
    """
    try:
        _retenir_ctx(kwargs)
        _releve_unique("transform_llm_output", kwargs)
        texte, cle = _reponse(kwargs)
        if texte is None:
            _noter({"event": "reponse_introuvable", "cles_kwargs": sorted(kwargs)})
            return None
        _appeler("observe", {
            "session_id": str(kwargs.get("session_id") or ""),
            "reply": texte,
        })
    except Exception as exc:
        _noter({"event": "erreur_hook", "hook": "transform_llm_output", "erreur": repr(exc)})
    return None


def on_skill_lifecycle(**kwargs):
    """Transmet un evenement de cycle de vie de skill.

    Hermes y signale un changement d'etat de l'enregistrement, avec son
    `use_count` : c'est la telemetrie du Curator, pas le resultat du tour. GK
    construit donc ses usages d'ici et ses echecs des corrections.
    """
    try:
        _retenir_ctx(kwargs)
        _releve_unique("on_skill_lifecycle", kwargs)
        nom = str(kwargs.get("skill_name") or "")
        if not nom:
            return None
        _appeler("skill_lifecycle", {
            "skill_name": nom,
            "action": str(kwargs.get("action") or ""),
            "session_id": str(kwargs.get("session_id") or ""),
            "task_id": str(kwargs.get("task_id") or ""),
            "use_count": kwargs.get("use_count"),
        })
    except Exception as exc:
        _noter({"event": "erreur_hook", "hook": "on_skill_lifecycle", "erreur": repr(exc)})
    return None


# -- initialisation ---------------------------------------------------------


HOOKS = (
    ("transform_llm_output", "on_transform_llm_output"),
    ("on_skill_lifecycle", "on_skill_lifecycle"),
)

# Noms possibles de la methode d'enregistrement. `PluginContext` se decrit
# comme « facade given to plugins so they can register tools and hooks », donc
# elle en porte une ; son nom exact n'est pas confirme.
METHODES_ENREGISTREMENT = ("register_hook", "add_hook", "hook", "on", "subscribe")


def _enregistrer(ctx) -> list[str]:
    """Enregistre les hooks via la methode que `ctx` expose reellement.

    Declarer les hooks dans `plugin.yaml` ne suffit pas : `gm-routing` declare
    `pre_verify` et `pre_tool_call` dans son manifeste tout en implementant
    `on_post_tool_call`, donc le manifeste ne cable rien — il declare.
    """
    faits = []
    for methode in METHODES_ENREGISTREMENT:
        fn = getattr(ctx, methode, None)
        if not callable(fn):
            continue
        for hook, nom_fonction in HOOKS:
            cible = globals().get(nom_fonction)
            if cible is None:
                continue
            try:
                fn(hook, cible)
                faits.append(f"{methode}({hook})")
            except Exception as exc:
                _noter({"event": "enregistrement_refuse", "methode": methode,
                        "hook": hook, "erreur": repr(exc)})
        if faits:
            return faits
    return faits


def _relever_api(ctx) -> dict:
    """Consigne ce que `ctx` offre vraiment, une fois.

    Deux inconnues de l'API de Hermes ont deja coute un aller-retour chacune.
    Autant les relever au chargement plutot que de les deviner.
    """
    try:
        membres = sorted(n for n in dir(ctx) if not n.startswith("_"))
    except Exception as exc:
        return {"erreur": repr(exc)}
    out = {"membres": membres}
    for nom in ("register_hook", "add_hook", "register_tool", "call_mcp", "get_config"):
        fn = getattr(ctx, nom, None)
        if fn is None:
            continue
        try:
            import inspect

            out[nom] = str(inspect.signature(fn))
        except Exception:
            out[nom] = "presente, signature illisible"
    return out


def register(ctx=None, *args, **kwargs):
    """Point d'entree. Il ENREGISTRE les hooks, et ne se contente pas de garder
    le contexte.

    C'est le defaut qui a empeche le premier essai de fonctionner : le contexte
    etait bien recu, mais jamais utilise pour brancher quoi que ce soit.

    Il ne leve jamais rien : on ne sait pas si un plugin qui plante au
    chargement empeche l'agent de demarrer, donc on se comporte comme si oui —
    et c'est la convention des plugins deja en place ici.
    """
    global _ctx
    try:
        if ctx is not None and hasattr(ctx, "call_mcp"):
            _ctx = ctx
            _noter({"event": "ctx_trouve", "ou": "argument du point d'entree"})
        else:
            _retenir_ctx(kwargs)
        if _ctx is not None:
            _noter({"event": "api_ctx", **_relever_api(_ctx)})
            faits = _enregistrer(_ctx)
            _noter({"event": "hooks_enregistres", "faits": faits or "AUCUN"})
        _noter({"event": "charge", "ctx_present": _ctx is not None})
    except Exception as exc:
        _noter({"event": "erreur_chargement", "erreur": repr(exc)})
    return None


# Le nom attendu n'est pas confirme. Des enveloppes distinctes plutot que des
# alias, pour que le releve dise LEQUEL Hermes appelle.
def setup(ctx=None, *a, **k):
    _noter({"event": "point_entree", "nom": "setup"})
    return register(ctx, *a, **k)


def init(ctx=None, *a, **k):
    _noter({"event": "point_entree", "nom": "init"})
    return register(ctx, *a, **k)


def activate(ctx=None, *a, **k):
    _noter({"event": "point_entree", "nom": "activate"})
    return register(ctx, *a, **k)


def on_load(ctx=None, *a, **k):
    _noter({"event": "point_entree", "nom": "on_load"})
    return register(ctx, *a, **k)
