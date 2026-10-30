"""gk-optimizer : l'optimiseur de prompt, cote Hermes.

Volontairement bete, pour la meme raison que gk-bridge : il vit dans le volume
persistant de Hermes, donc le modifier veut dire toucher a la production, et
l'API de plugin appartient a un projet tiers qui bouge. Cinquante lignes sans
etat se reecrivent en une heure quand un point d'accroche est renomme ; c'est
cela l'assurance, pas la compatibilite. Toute l'intelligence est dans GK.

**Il ne comprend pas ce qu'il transporte.** Le bloc est une chaine opaque : il
ne sait pas s'il porte un arret, une lecture corrigee ou les deux. Changer le
format du bloc cote serveur ne demande donc aucun redeploiement ici.

Pourquoi un plugin separe de gk-bridge
--------------------------------------

Parce qu'il est sur le chemin d'ENTREE du tour, et gk-bridge non. Les deux
risques ne se ressemblent pas, et les separer permet de couper celui-ci seul :
renommer `plugin.yaml` en `plugin.yaml.inactif` desactive l'optimiseur sans
arreter la chaine de mesure, qui est ce qu'on veut garder le plus longtemps.

Quatre garanties, et ce sont elles qu'il faut relire avant de toucher au fichier
----------------------------------------------------------------------------

1. **Le message de l'utilisateur n'est jamais reecrit.** Le bloc est ajoute
   apres lui. Le texte d'origine reste un prefixe de ce qui part au modele.
2. **Jamais plus d'une seconde et demie d'attente.** L'appel part sur un fil et
   on l'abandonne au-dela : GK lent ou mort rend un tour inchange, jamais un
   tour bloque. Le plancher de `call_mcp` chez Hermes est d'une seconde.
3. **Un coupe-circuit.** Apres ECHECS_AVANT_COUPURE echecs d'affilee, plus
   aucun appel pendant COUPURE secondes. Sans lui, un GK arrete ajouterait son
   timeout a chaque message de la journee.
4. **Rien ne remonte.** Chaque hook et le chargement sont enveloppes.

Ce qu'il decouvre au lieu de le supposer
----------------------------------------

Trois inconnues de l'API de Hermes, cherchees parmi les possibilites plausibles
et consignees au premier appel dans `/opt/data/gk-optimizer.jsonl` :

* par ou arrive le `PluginContext` — reconnu a son `call_mcp` ;
* sous quel nom le message de l'utilisateur est passe a `pre_llm_call`, ou s'il
  arrive dans une liste `messages` ;
* ce que Hermes fait de la valeur de retour. Les deux voies plausibles sont
  essayees : si le message est dans une liste, on le modifie sur place et on
  renvoie None ; sinon on renvoie la chaine. Le releve dit laquelle a servi, et
  c'est ce fichier qu'il faudra lire apres le premier message reel.
"""

from __future__ import annotations

import json
import os
import threading
import time

JOURNAL = os.environ.get("GK_OPTIMIZER_LOG", "/opt/data/gk-optimizer.jsonl")
SERVEUR_MCP = "gk"
TIMEOUT = 1.0          # plancher impose par Hermes : call_mcp borne a 1-600 s
ATTENTE_MAX = 1.5      # au-dela, on abandonne le fil et le tour continue
ECHECS_AVANT_COUPURE = 3
COUPURE = 60.0

# Noms sous lesquels le message de l'utilisateur peut arriver. Le premier
# present gagne, et le releve dit lequel.
CLES_MESSAGE = (
    "message", "text", "prompt", "user_message", "user_input", "content", "input", "query", "task"
)
CLES_LISTE = ("messages", "conversation", "history")

# Garde-fou minimal en dur, au cas ou GK serait absent au chargement. L'etape 5
# veut que la section de prompt vienne du serveur, pour etre affinable sans
# redeploiement ; celle-ci n'est qu'un filet.
SECTION_MINIMALE = (
    "Si un message porte une ligne « [GK] STOP », pose la question qu'elle contient et "
    "n'execute rien d'autre avant la reponse de l'utilisateur."
)

_ctx = None
_releve_fait = False
_section_posee = False
_echecs = 0
_rouvre_a = 0.0
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
    """Trouve le PluginContext, ou qu'il arrive. Reconnu a ce qu'il sait faire
    plutot qu'a son nom ou son type."""
    global _ctx
    if _ctx is not None:
        return _ctx
    for cle, valeur in kwargs.items():
        if hasattr(valeur, "call_mcp"):
            _ctx = valeur
            _noter({"event": "ctx_trouve", "ou": f"kwargs[{cle!r}]"})
            return _ctx
    return None


def _releve_unique(hook: str, kwargs: dict) -> None:
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


def _message(kwargs: dict):
    """Trouve le message de l'utilisateur. Renvoie (texte, cle, porteur).

    `porteur` est le dictionnaire du message dans une liste, quand c'est de la
    qu'il vient : c'est lui qu'on modifiera sur place, puisque dans ce cas la
    valeur de retour du hook n'a aucune raison d'etre lue.
    """
    for cle in CLES_MESSAGE:
        valeur = kwargs.get(cle)
        if isinstance(valeur, str) and valeur.strip():
            return valeur, cle, None
    for cle in CLES_LISTE:
        liste = kwargs.get(cle)
        if not isinstance(liste, list):
            continue
        for element in reversed(liste):
            if not isinstance(element, dict):
                continue
            if element.get("role") not in (None, "user", "human"):
                continue
            contenu = element.get("content")
            if isinstance(contenu, str) and contenu.strip():
                return contenu, f"{cle}[-1].content", element
            if isinstance(contenu, list):
                for bloc in reversed(contenu):
                    if isinstance(bloc, dict) and isinstance(bloc.get("text"), str):
                        if bloc["text"].strip():
                            return bloc["text"], f"{cle}[-1].content[].text", bloc
    return None, None, None


def _charge(resultat) -> dict:
    """Deballe ce que `call_mcp` a rendu, et rend la charge utile de GK.

    La forme de retour n'est pas confirmee, d'ou l'acceptation defensive : un
    dictionnaire direct, l'enveloppe MCP
    `{"content": [{"type": "text", "text": "<json>"}]}`, une chaine JSON, ou un
    objet portant `.content`. Un format non reconnu rend un dictionnaire vide,
    donc un tour inchange.

    C'est la charge entiere qui est rendue et non le seul bloc : la section de
    prompt arrive par le meme chemin, et deux deballages du meme objet se
    desynchroniseraient.
    """
    if resultat is None:
        return {}
    if isinstance(resultat, str):
        try:
            resultat = json.loads(resultat)
        except Exception:
            return {}
    if not isinstance(resultat, dict) and hasattr(resultat, "content"):
        resultat = {"content": resultat.content}
    if not isinstance(resultat, dict):
        return {}
    if isinstance(resultat.get("block"), str):
        return resultat
    contenu = resultat.get("content")
    if isinstance(contenu, list):
        for element in contenu:
            texte = None
            if isinstance(element, dict):
                texte = element.get("text")
            elif hasattr(element, "text"):
                texte = element.text
            if not isinstance(texte, str):
                continue
            try:
                charge = json.loads(texte)
            except Exception:
                continue
            if isinstance(charge, dict):
                return charge
    return {}


def _demander(session_id: str, texte: str) -> str:
    """Appelle GK et rend le bloc, ou une chaine vide. Ne leve jamais.

    L'appel part sur un fil que l'on abandonne passe ATTENTE_MAX : c'est la
    seule facon de garantir un plafond de latence sur le chemin d'entree sans
    rien supposer du respect du timeout par `call_mcp`.
    """
    global _echecs, _rouvre_a
    ctx = _ctx
    if ctx is None:
        return ""
    if time.time() < _rouvre_a:
        return ""

    resultat: list = []

    def _tache():
        try:
            resultat.append(
                ctx.call_mcp(
                    SERVEUR_MCP,
                    "optimize",
                    {"session_id": session_id, "text": texte},
                    timeout=TIMEOUT,
                )
            )
        except PermissionError as exc:
            # Defaut-refus de Hermes : le serveur n'est pas dans
            # plugins.entries.gk-optimizer.mcp_allowlist du config.yaml.
            resultat.append(exc)
        except BaseException as exc:  # noqa: BLE001
            # BaseException et non Exception, uniquement parce que nous sommes
            # sur un fil : ce qui s'echappe ici ne peut etre rattrape par
            # personne et finit en trace dans la sortie de l'agent. Rien n'a
            # besoin de remonter, le tour continuant sans nous dans tous les cas.
            resultat.append(exc)

    fil = threading.Thread(target=_tache, name="gk-optimizer", daemon=True)
    fil.start()
    fil.join(ATTENTE_MAX)

    if not resultat:
        _compter_echec("delai_depasse")
        return ""
    if isinstance(resultat[0], BaseException):
        _compter_echec(repr(resultat[0]))
        return ""

    charge = _charge(resultat[0])
    _section(charge)
    with _lock:
        if _echecs:
            _noter({"event": "coupe_circuit_referme"})
        _echecs = 0
    bloc = charge.get("block")
    return bloc if isinstance(bloc, str) else ""


def _compter_echec(raison: str) -> None:
    global _echecs, _rouvre_a
    with _lock:
        _echecs += 1
        ouvre = _echecs >= ECHECS_AVANT_COUPURE
        if ouvre:
            _rouvre_a = time.time() + COUPURE
            _echecs = 0
    _noter({"event": "echec", "raison": raison, "coupe_circuit_ouvert": ouvre})


def _section(charge: dict) -> None:
    """Enregistre la section de prompt servie par GK, une seule fois.

    Elle est demandee au serveur et non codee ici pour pouvoir etre affinee sans
    redeploiement (etape 5). Si le point d'accroche n'existe pas, on n'insiste
    pas : le bloc d'arret porte son instruction en clair, donc la section n'est
    qu'un renfort.
    """
    global _section_posee
    with _lock:
        if _section_posee:
            return
        _section_posee = True
    texte = SECTION_MINIMALE
    try:
        if isinstance(charge.get("prompt_section"), str):
            texte = charge["prompt_section"] or texte
        poser = getattr(_ctx, "register_system_prompt_section", None)
        if callable(poser):
            poser(texte)
            _noter({"event": "section_posee", "longueur": len(texte)})
        else:
            _noter({"event": "section_impossible", "raison": "pas de point d'accroche"})
    except Exception as exc:
        _noter({"event": "section_echouee", "erreur": repr(exc)})


# -- hook -------------------------------------------------------------------


def on_pre_llm_call(**kwargs):
    """Ajoute le bloc de GK au message de l'utilisateur, ou ne fait rien.

    Renvoie None des que quelque chose manque : pas de contexte, pas de message
    reconnu, GK muet, bloc vide. Un tour inchange est le comportement par
    defaut, pas le cas d'erreur.
    """
    try:
        _retenir_ctx(kwargs)
        _releve_unique("pre_llm_call", kwargs)
        texte, cle, porteur = _message(kwargs)
        if texte is None:
            _noter({"event": "message_introuvable", "cles_kwargs": sorted(kwargs)})
            return None

        bloc = _demander(str(kwargs.get("session_id") or ""), texte)
        if not bloc:
            return None

        # AJOUT, jamais substitution : le texte d'origine reste un prefixe.
        nouveau = texte + "\n\n" + bloc

        if porteur is not None:
            # Le message vient d'une liste : on le modifie sur place, parce que
            # dans ce cas la valeur de retour du hook n'a aucune raison d'etre
            # lue, et on renvoie None pour ne pas risquer un double ajout.
            if isinstance(porteur.get("content"), str):
                porteur["content"] = nouveau
            elif isinstance(porteur.get("text"), str):
                porteur["text"] = nouveau
            _noter({"event": "ajoute", "voie": "mutation", "cle": cle, "octets": len(bloc)})
            return None

        _noter({"event": "ajoute", "voie": "retour", "cle": cle, "octets": len(bloc)})
        return nouveau
    except Exception as exc:
        _noter({"event": "erreur_hook", "hook": "pre_llm_call", "erreur": repr(exc)})
        return None


# -- initialisation ---------------------------------------------------------


def register(ctx=None, *args, **kwargs):
    """Point d'entree, si Hermes en appelle un. Son nom exact n'est pas
    confirme, d'ou les alias plus bas. Il ne leve jamais rien."""
    global _ctx
    try:
        if ctx is not None and hasattr(ctx, "call_mcp"):
            _ctx = ctx
            _noter({"event": "ctx_trouve", "ou": "argument de register"})
        else:
            _retenir_ctx(kwargs)
        _noter({"event": "charge", "ctx_present": _ctx is not None})
    except Exception as exc:
        _noter({"event": "erreur_chargement", "erreur": repr(exc)})
    return None


setup = register
init = register
activate = register
on_load = register
