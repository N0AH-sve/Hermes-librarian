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

IL N'INJECTE RIEN. Aucun `pre_llm_call`, donc le chemin d'entree de l'agent
n'est pas touche, et un test le verifie. La phase 2 ajoute le **facteur** : le
pont pousse le catalogue des skills vers GK et lui renvoie les tranches qu'il
reclame. Toujours apres que l'agent a repondu, sur un fil detache, avec un
budget de temps. Le comportement de gb reste strictement inchange.

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

# `response_text` est le nom reel, releve le 2026-10-22 : les kwargs de
# `transform_llm_output` sont {model, platform, response_text, session_id,
# telemetry_schema_version, turn_id}. Les suivants restent en repli au cas ou
# Hermes renomme — un detecteur aveugle serait pire qu'un detecteur bruyant, et
# `reponse_introuvable` signale de toute facon l'echec.
CLES_REPONSE = ("response_text", "text", "output", "content", "response", "message")

TIMEOUT_CATALOGUE = 30.0       # rien n'attend : le fil est detache
INTERVALLE_CATALOGUE = 3600.0  # au plus une ronde par heure en regime normal
INTERVALLE_FORCE = 60.0        # plancher quand GK reclame explicitement
BUDGET_RONDE = 25.0            # au-dela on abandonne, le manifeste se repare seul

_ctx = None          # PluginContext, des qu'on l'a vu passer
_releve_fait = False
_forme_relevee = False
_lock = threading.Lock()
_catalogue_mod = None
_catalogue_mtime = 0.0
_dernier_catalogue = 0.0
_ronde_active = False


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


def _appeler(outil: str, arguments: dict, suite=None) -> None:
    """Envoie sur un fil detache. Jamais d'attente sur le chemin d'une reponse.

    `transform_llm_output` est sur le trajet de la reponse a l'utilisateur :
    attendre GK y ajouterait sa latence. Le resultat est tout de meme lu **sur
    le fil**, parce qu'il porte les `besoins` de GK — le canal par lequel il
    reclame son catalogue sans jamais parler le premier.
    """
    ctx = _ctx
    if ctx is None:
        return

    def _tache():
        try:
            resultat = ctx.call_mcp(SERVEUR_MCP, outil, arguments, timeout=TIMEOUT)
        except PermissionError as exc:
            # Defaut-refus de Hermes : le serveur n'est pas dans
            # plugins.entries.gk-bridge.mcp_allowlist du config.yaml.
            _noter({"event": "refuse", "outil": outil, "erreur": repr(exc)})
            return
        except Exception as exc:
            _noter({"event": "echec", "outil": outil, "erreur": repr(exc)})
            return
        if suite is None:
            return
        try:
            suite(resultat)
        except Exception as exc:
            _noter({"event": "erreur_suite", "outil": outil, "erreur": repr(exc)})

    threading.Thread(target=_tache, name="gk-bridge", daemon=True).start()


# -- le facteur : catalogue et tranches -------------------------------------


def _charger_catalogue():
    """Charge `catalogue.py`, voisin de ce fichier, et le **relit quand il
    change**.

    C'est ce qui rend les corrections suivantes deployables par une simple copie
    de fichier, sans redemarrer l'agent — contrainte reelle : un redemarrage
    coute deux a trois minutes pendant lesquelles personne ne peut travailler.

    Tout ce qui est susceptible de bouger vit donc dans `catalogue.py` : la
    lecture du resultat MCP, le manifeste, le decoupage, la ronde. Ce fichier-ci
    ne garde que les hooks, le journal et la cadence, c'est-a-dire ce qui ne
    peut de toute facon pas changer a chaud puisque les hooks sont enregistres au
    chargement.

    L'import se fait **par chemin et jamais par import relatif**, bien que le
    relatif marche (releve du 2026-10-22 : `gk-bridge` est importe comme un
    paquet malgre le tiret). Un module importe reste dans `sys.modules` et ne
    serait donc jamais relu : le rechargement a chaud exige un `exec_module` sur
    une specification neuve.

    Un echec est sans consequence : l'observation continue sans le facteur.
    """
    global _catalogue_mod, _catalogue_mtime
    chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogue.py")
    try:
        mtime = os.stat(chemin).st_mtime
    except OSError:
        mtime = 0.0
    if _catalogue_mod is not None and mtime == _catalogue_mtime:
        return _catalogue_mod
    recharge = _catalogue_mod is not None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("gk_bridge_catalogue", chemin)
        if spec is None or spec.loader is None:
            raise ImportError(chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _catalogue_mod, _catalogue_mtime = module, mtime
        _noter({"event": "catalogue_charge", "voie": "chemin", "recharge": recharge,
                "mtime": mtime})
        return _catalogue_mod
    except Exception as exc:
        if _catalogue_mod is not None:
            # Une version cassee ne doit pas faire perdre celle qui marchait.
            _noter({"event": "recharge_refusee", "erreur": repr(exc)})
            return _catalogue_mod
        _noter({"event": "catalogue_absent", "erreur": repr(exc)})
        return None


def _extraire(resultat) -> dict:
    """Delegue a `catalogue.extraire`, qui est remplacable a chaud.

    Le repli minimal ne sert qu'au cas ou `catalogue.py` manquerait : il defait
    l'enveloppe de Hermes et rien de plus. L'observation de la phase 1 ne doit
    jamais dependre du fichier rechargeable.
    """
    module = _charger_catalogue()
    if module is not None and hasattr(module, "extraire"):
        return module.extraire(resultat, _noter)
    if isinstance(resultat, dict):
        charge = resultat.get("result")
        if isinstance(charge, dict):
            return charge
    return {}


def _ronde_due(force: bool) -> bool:
    """Un verrou et deux horloges : une ronde par heure en regime normal, et au
    plus une par minute quand GK reclame. Sans ce plancher, un skill dont
    l'empreinte bouge sans cesse declencherait une ronde a chaque tour."""
    global _dernier_catalogue, _ronde_active
    with _lock:
        if _ronde_active:
            return False
        ecoule = time.time() - _dernier_catalogue
        if ecoule < (INTERVALLE_FORCE if force else INTERVALLE_CATALOGUE):
            return False
        _ronde_active = True
        _dernier_catalogue = time.time()
        return True


def _ronde_catalogue(force: bool = False) -> None:
    """Declenche une ronde. Le travail est dans `catalogue.ronde`, remplacable a
    chaud ; ce qui reste ici est la cadence et le verrou, qui tiennent l'etat du
    processus et ne peuvent donc pas vivre dans un module recharge.

    Tourne sur le fil detache de l'appel qui l'a declenchee, donc apres que
    l'agent a repondu.
    """
    global _ronde_active
    ctx = _ctx
    if ctx is None or not _ronde_due(force):
        return
    resume: dict = {"event": "ronde_catalogue", "force": force}
    try:
        module = _charger_catalogue()
        if module is None:
            resume["abandon"] = "catalogue.py indisponible"
        else:
            resume = dict(
                module.ronde(
                    ctx, _noter, serveur=SERVEUR_MCP,
                    timeout=TIMEOUT_CATALOGUE, budget=BUDGET_RONDE,
                ),
                force=force,
            )
    except Exception as exc:
        resume["erreur"] = repr(exc)
    finally:
        with _lock:
            _ronde_active = False
        _noter(resume)


def _suite_observe(resultat) -> None:
    """Lit les `besoins` de GK et declenche une ronde s'il en a besoin.

    `catalogue_vide` est ce qui permet a un GK redemarre avec une base neuve de
    se reconstruire sans que personne n'intervienne.
    """
    besoins = _extraire(resultat).get("besoins") or []
    _ronde_catalogue(force=bool({"catalogue_vide", "tranches_attendues"} & set(besoins)))


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
            # `turn_id` rend l'attribution exacte au lieu de reposer sur
            # « la correction en attente la plus recente de la session ».
            "turn_id": str(kwargs.get("turn_id") or ""),
            "reply": texte,
        }, suite=_suite_observe)
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
            "turn_id": str(kwargs.get("turn_id") or ""),
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

def _enregistrer(ctx) -> list[str]:
    """Branche les hooks par `ctx.register_hook`, verifie dans la source de
    Hermes (`hermes_cli/plugins.py:916`) et dans le `register()` de
    `gm-routing`.

    Declarer les hooks dans `plugin.yaml` ne suffit pas : `gm-routing` declare
    `pre_verify` et `pre_tool_call` dans son manifeste tout en en enregistrant
    cinq ici. Le manifeste declare, il ne cable pas.
    """
    faits = []
    for hook, nom_fonction in HOOKS:
        cible = globals().get(nom_fonction)
        if cible is None:
            continue
        try:
            ctx.register_hook(hook, cible)
            faits.append(hook)
        except Exception as exc:
            _noter({"event": "enregistrement_refuse", "hook": hook, "erreur": repr(exc)})
    return faits


def register(ctx=None, *args, **kwargs):
    """Point d'entree. Signature verifiee : `register(ctx)`, comme `gm-routing`.

    Il ENREGISTRE les hooks au lieu de seulement garder le contexte. C'est le
    defaut qui a fait echouer le premier essai : le contexte etait bien recu,
    jamais utilise pour brancher quoi que ce soit.

    Il ne leve jamais rien : on ne sait pas si un plugin qui plante au
    chargement empeche l'agent de demarrer, donc on se comporte comme si oui —
    et c'est la convention des plugins deja en place ici.
    """
    global _ctx
    try:
        if ctx is not None and hasattr(ctx, "call_mcp"):
            _ctx = ctx
        else:
            _retenir_ctx(kwargs)
        if _ctx is None:
            _noter({"event": "charge", "ctx_present": False})
            return None
        faits = _enregistrer(_ctx)
        _noter({"event": "charge", "ctx_present": True, "hooks": faits or "AUCUN"})
    except Exception as exc:
        _noter({"event": "erreur_chargement", "erreur": repr(exc)})
    return None
