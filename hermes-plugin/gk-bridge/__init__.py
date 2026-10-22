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


def _extraire(resultat) -> dict:
    """Ramene la charge utile d'un appel MCP a un dictionnaire.

    La forme exacte que Hermes rend n'est pas connue a l'ecriture : ce peut etre
    le dictionnaire de l'outil, un `structuredContent`, ou une liste de contenus
    dont le premier porte du JSON en texte. On essaie, et la forme reellement
    rencontree est journalisee une fois. Meme methode que pour `response_text`,
    qu'aucune de nos sept suppositions n'avait trouve.
    """
    global _forme_relevee
    try:
        if resultat is None:
            return {}
        contenus = None
        if isinstance(resultat, dict):
            for cle in ("wanted", "besoins", "ok", "v"):
                if cle in resultat:
                    return resultat
            for cle in ("structuredContent", "structured_content", "result", "data"):
                interne = resultat.get(cle)
                if isinstance(interne, dict):
                    return interne
            contenus = resultat.get("content")
        else:
            struct = getattr(resultat, "structuredContent", None)
            if isinstance(struct, dict):
                return struct
            contenus = getattr(resultat, "content", None)
        if isinstance(contenus, (list, tuple)):
            for bloc in contenus:
                texte = (
                    bloc.get("text") if isinstance(bloc, dict)
                    else getattr(bloc, "text", None)
                )
                if isinstance(texte, str) and texte.strip().startswith("{"):
                    return json.loads(texte)
        return {}
    except Exception as exc:
        if not _forme_relevee:
            _forme_relevee = True
            _noter({"event": "forme_inconnue", "type": repr(type(resultat)),
                    "erreur": repr(exc)})
        return {}


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
    """Charge `catalogue.py`, voisin de ce fichier.

    Deux voies, parce que le dossier s'appelle `gk-bridge` : un tiret n'est pas
    un nom de module valide, donc on ne sait pas sous quel nom Hermes a importe
    ce paquet. L'echec est sans consequence : le pont de la phase 1 continue de
    fonctionner sans le facteur.
    """
    global _catalogue_mod
    if _catalogue_mod is not None:
        return _catalogue_mod
    try:
        from . import catalogue as module  # type: ignore

        _catalogue_mod = module
        _noter({"event": "catalogue_charge", "voie": "relatif"})
        return _catalogue_mod
    except Exception:
        pass
    try:
        import importlib.util

        chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogue.py")
        spec = importlib.util.spec_from_file_location("gk_bridge_catalogue", chemin)
        if spec is None or spec.loader is None:
            raise ImportError(chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _catalogue_mod = module
        _noter({"event": "catalogue_charge", "voie": "chemin"})
        return _catalogue_mod
    except Exception as exc:
        _noter({"event": "catalogue_absent", "erreur": repr(exc)})
        return None


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
    """Une ronde complete : le manifeste, puis les tranches tant que GK en veut.

    Tourne sur le fil detache de l'appel qui l'a declenchee, donc apres que
    l'agent a repondu. Bornee par `BUDGET_RONDE` : ce qui ne passe pas cette
    fois passera a la ronde suivante, le manifeste etant auto-reparateur.
    """
    global _ronde_active
    ctx = _ctx
    if ctx is None or not _ronde_due(force):
        return
    debut = time.monotonic()
    resume = {"event": "ronde_catalogue", "force": force}
    try:
        module = _charger_catalogue()
        if module is None:
            return
        entrees, complet, diag = module.manifeste()
        resume.update(diag)
        resume["complet"] = complet
        if not entrees:
            resume["abandon"] = "manifeste vide"
            return
        reponse = _extraire(ctx.call_mcp(
            SERVEUR_MCP, "catalog",
            {"skills": entrees, "complete": complet},
            timeout=TIMEOUT_CATALOGUE,
        ))
        resume["manifeste_envoye"] = len(entrees)
        resume["nouveaux"] = reponse.get("nouveaux")
        resume["modifies"] = reponse.get("modifies")
        voulus = list(reponse.get("wanted") or [])
        intitules = list(reponse.get("headings") or [])
        resume["intitules"] = len(intitules)
        envoyees = tours = 0
        racine = diag.get("racine") or module.RACINE_DEFAUT
        while voulus and intitules and (time.monotonic() - debut) < BUDGET_RONDE:
            items = module.tranches_pour(racine, voulus, intitules)
            if not items:
                break
            suivant = _extraire(ctx.call_mcp(
                SERVEUR_MCP, "sections", {"items": items}, timeout=TIMEOUT_CATALOGUE
            ))
            envoyees += len(items)
            tours += 1
            restants = list(suivant.get("wanted") or [])
            if restants == voulus:
                # GK redemande exactement la meme chose : on s'arrete plutot que
                # de boucler. La ronde suivante reessaiera.
                resume["bloque_sur"] = len(restants)
                break
            voulus = restants
        resume["tranches_envoyees"] = envoyees
        resume["tours"] = tours
        resume["restants"] = len(voulus)
    except PermissionError as exc:
        resume["erreur"] = "refuse: %r" % (exc,)
    except Exception as exc:
        resume["erreur"] = repr(exc)
    finally:
        resume["duree_s"] = round(time.monotonic() - debut, 2)
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
