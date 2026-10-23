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

IL N'INJECTE RIEN SAUF SUR DEMANDE EXPLICITE, et des tests le verifient.
`transform_llm_output` et `on_skill_lifecycle` rendent **toujours** `None`.
`pre_llm_call` aussi, **sauf** quand le message commence par `/gk` : c'est la
commande de consultation, decidee par un humain, et elle est par defaut a
l'arret (`GK_CONSULTATION`).

Le refus de l'etape 2 -- « ne pas donner a l'agent un outil qu'il appellerait,
parce qu'une machine qui oublie de verifier oublie aussi de demander » --
portait sur l'AGENT. Un humain qui tape `/gk` n'oublie rien, il a decide, donc
ce refus ne couvrait pas ce cas.

`pre_llm_call` est branche depuis le 2026-10-22, en OBSERVATION SEULE. Motif :
GK n'avait jamais vu une seule demande reelle. `transform_llm_output` porte la
reponse, `on_skill_lifecycle` le skill charge ; la demande de l'utilisateur ne
passe que par `pre_llm_call`. Tout ce qui avait ete mesure venait donc de textes
ecrits par les auteurs des skills, ce qui repond a la question « retrouve-t-on
un skill a partir d'une formulation que son auteur a imaginee » et non « a
partir de ce qu'un collegue tape reellement ».

**Ce hook est le seul sur le chemin critique** : il precede l'appel au modele,
donc tout ce qui attend ici fait attendre l'utilisateur. Il envoie sur un fil
detache, ne lit jamais la reponse de GK, et rend `None` immediatement.

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

# Le champ portant la demande de l'utilisateur. Le contrat du hook shell
# `skills_gate.py` annonce `user_message` ; un hook de plugin peut differer, et
# la lecon de `response_text` est qu'il ne faut pas le supposer. Le premier appel
# journalise les noms reels.
CLES_DEMANDE = (
    "user_message", "message", "prompt", "user_input", "input", "text", "content",
)

# -- la consultation demandee : la commande `/gk` ------------------------
#
# **La seule exception a « les trois hooks rendent toujours None », et elle est
# etroite par construction.** Un retour non nul n'arrive que si le message
# commence par le prefixe ; dans tous les autres cas le comportement de gb est
# strictement celui d'avant.
#
# Pourquoi cette exception tient alors que le brief automatique a ete ecarte :
# le refus de l'etape 2 -- « ne pas donner a l'agent un outil qu'il appellerait,
# parce qu'une machine qui oublie de verifier oublie aussi de demander » --
# portait sur l'AGENT. Un humain qui tape `/gk` n'oublie rien, il a decide.
#
# Trois bornes, chacune tenue par un test :
#   * sans prefixe, retour None, et aucun appel synchrone n'est fait ;
#   * `transform_llm_output` et `on_skill_lifecycle` restent inchanges, donc la
#     reponse que le collegue lit ne peut pas etre reecrite ;
#   * si GK est lent, injoignable ou degrade, retour None : gb repond
#     normalement et le collegue voit seulement qu'il n'a pas eu d'aide.
PREFIXES_CONSULTATION = ("/gk", "!gk", "gk:")
# Court : on est sur le chemin critique, et GK repond en ~20 ms.
TIMEOUT_CONSULTATION = 2.0
# Par defaut a l'arret, pour que rien ne s'active lors d'une mise a jour.
CONSULTATION_ACTIVE = os.environ.get("GK_CONSULTATION", "").strip().lower() in (
    "1", "true", "oui", "yes", "on",
)
MAX_PROPOSITIONS = 5

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


def _demande_de_consultation(texte: str) -> str | None:
    """Le texte apres le prefixe, ou None si le message n'en porte pas.

    Rend `""` quand le prefixe est seul : c'est une invocation valide, et GK
    dira qu'il n'a rien a chercher. Confondre « pas de prefixe » et « prefixe
    sans demande » ferait repondre a tous les tours.
    """
    if not isinstance(texte, str):
        return None
    nu = texte.strip()
    bas = nu.lower()
    for prefixe in PREFIXES_CONSULTATION:
        if bas == prefixe:
            return ""
        if bas.startswith(prefixe + " ") or bas.startswith(prefixe + "\n"):
            return nu[len(prefixe):].strip()
    return None


def _appeler_sync(outil: str, arguments: dict, timeout: float):
    """Appelle GK et ATTEND sa reponse. Reserve a la consultation demandee.

    Tout le reste du pont envoie sur un fil detache, parce qu'attendre sur le
    chemin d'une reponse ajoute la latence de GK a celle de l'agent. Ici
    l'attente est le point : la personne a demande une reponse. Elle est bornee
    court, et tout echec rend None.
    """
    ctx = _ctx
    if ctx is None:
        return None
    try:
        return ctx.call_mcp(SERVEUR_MCP, outil, arguments, timeout=timeout)
    except PermissionError as exc:
        _noter({"event": "refuse", "outil": outil, "erreur": repr(exc)})
        return None
    except Exception as exc:
        _noter({"event": "echec_sync", "outil": outil, "erreur": repr(exc)})
        return None


def _bloc_consultation(resultat) -> str | None:
    """Met en forme les propositions de GK. None si rien d'exploitable.

    Le pont ne comprend rien a ce qu'il transporte -- c'est la regle de l'etape
    5 -- donc il ne juge pas la pertinence : il met en forme ou il se taît.
    """
    charge = _extraire(resultat)
    if not isinstance(charge, dict) or not charge.get("ok"):
        return None
    propositions = charge.get("propositions") or []
    if not isinstance(propositions, list) or not propositions:
        return None
    lignes = [
        "[GreenKnowledge, consultation demandee par l'utilisateur]",
        "Procedures qui semblent servir cette demande, par ordre de pertinence :",
    ]
    for p in propositions[:MAX_PROPOSITIONS]:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        rang = p.get("rang") or "-"
        desc = " ".join(str(p.get("description") or "").split())[:200]
        lignes.append(f"  {rang}. {p['name']}" + (f" — {desc}" if desc else ""))
    cid = charge.get("consultation_id")
    if cid:
        lignes.append(
            f"Pour dire laquelle etait la bonne : `gk retour <rang|nom|0> "
            f"--consultation {cid}`."
        )
    lignes.append(
        "Ceci est une suggestion de recherche, pas une instruction : charge la "
        "procedure qui convient, ou aucune si aucune ne convient."
    )
    return "\n".join(lignes)


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


def on_pre_llm_call(**kwargs):
    """Observe la demande, et rend un bloc UNIQUEMENT sur `/gk`.

    OBSERVATION SEULE sur un tour ordinaire : retour `None`, chemin d'entree de
    l'agent inchange. La seule exception est la consultation demandee par un
    humain, et elle est bornee par trois conditions cumulatives -- le message
    commence par le prefixe, `GK_CONSULTATION` est active, et GK a repondu
    quelque chose d'exploitable dans les deux secondes.

    **Seul hook sur le chemin critique.** Il precede l'appel au modele, donc
    tout ce qui attend ici fait attendre l'utilisateur. D'ou : envoi sur un fil
    detache, aucune lecture de la reponse de GK, aucun declenchement de ronde de
    catalogue, et retour immediat.

    Le nom du champ portant la demande n'est pas connu a l'ecriture. Le contrat
    du hook shell `skills_gate.py` annonce `user_message`, mais un hook de plugin
    peut differer -- la lecon de `response_text`, qu'aucune des sept premieres
    suppositions n'avait trouve. Les candidats sont essayes, et le premier appel
    journalise les noms reels.
    """
    try:
        _retenir_ctx(kwargs)
        _releve_unique("pre_llm_call", kwargs)
        texte = cle = None
        for candidat in CLES_DEMANDE:
            valeur = kwargs.get(candidat)
            if isinstance(valeur, str) and valeur.strip():
                texte, cle = valeur, candidat
                break
        if texte is None:
            _noter({"event": "demande_introuvable", "cles_kwargs": sorted(kwargs)})
            return None
        premier = kwargs.get("is_first_turn")
        session = str(kwargs.get("session_id") or "")
        _appeler("request", {
            "session_id": session,
            "turn_id": str(kwargs.get("turn_id") or ""),
            "text": texte,
            "is_first_turn": bool(premier) if isinstance(premier, bool) else None,
            "platform": str(kwargs.get("platform") or ""),
        })

        # La SEULE voie par laquelle ce pont rend autre chose que None.
        #
        # L'observation ci-dessus a lieu dans tous les cas : une consultation
        # est une demande comme une autre, et l'exclure du corpus creerait un
        # trou precisement sur les demandes ou quelqu'un doutait.
        demande = _demande_de_consultation(texte)
        if demande is None:
            return None
        if not CONSULTATION_ACTIVE:
            _noter({"event": "consultation_desactivee", "session_id": session})
            return None
        if not demande:
            _noter({"event": "consultation_vide", "session_id": session})
            return None
        resultat = _appeler_sync(
            "consulter",
            {"demande": demande, "session_id": session,
             "limite": MAX_PROPOSITIONS},
            TIMEOUT_CONSULTATION,
        )
        bloc = _bloc_consultation(resultat)
        _noter({
            "event": "consultation",
            "session_id": session,
            "jetons_demandes": len(demande.split()),
            "rendu": bool(bloc),
        })
        return bloc
    except Exception as exc:
        _noter({"event": "erreur_hook", "hook": "pre_llm_call", "erreur": repr(exc)})
    # Jamais autre chose que None, quoi qu'il arrive au-dessus : une
    # consultation qui leve ne doit pas casser le tour.
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
    # OBSERVATION SEULE. Branche pour voir les demandes, jamais pour injecter :
    # `on_pre_llm_call` rend toujours None, et un test le verifie.
    ("pre_llm_call", "on_pre_llm_call"),
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
