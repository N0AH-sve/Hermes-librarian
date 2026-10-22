"""Le facteur : construire le manifeste, et decouper les tranches demandees.

Separe de `__init__.py` a dessein. Le pont de la phase 1 fait cinquante lignes
sans etat, et c'est ce qui le rend bon marche a reecrire quand Hermes renomme un
point d'accroche. Le manifeste est plus volumineux ; le tenir a part garde la
partie critique — celle qui vit sur le trajet d'une reponse a l'utilisateur —
aussi courte qu'avant.

Ce module ne comprend rien non plus. Il ne sait pas ce qu'est un declencheur :
**GK lui envoie la liste des intitules a chercher**, il tranche sur ces chaines
et renvoie les morceaux. Ajouter une rubrique plus tard sera donc une ligne de
configuration dans GK, et plus jamais un redemarrage de l'agent.

Trois contraintes dictent la forme du code :

* **ne jamais ralentir un tour.** Tout tourne sur un fil detache, apres que
  l'agent a repondu, avec un budget de temps ;
* **ne jamais lire 3,6 Mo.** L'empreinte vient de `(taille, mtime)`, pas d'un
  hachage du contenu, et seul le frontmatter est lu — 2 Ko par fichier. Les
  corps ne sont ouverts que pour les skills que GK reclame ;
* **ne jamais lever.** Un manifeste manque, le suivant repart : la boucle est
  auto-reparatrice.
"""

from __future__ import annotations

import json
import os
import time
import unicodedata

RACINE_DEFAUT = os.environ.get("GK_SKILLS_ROOT", "/opt/data/skills")

FRONTMATTER_OCTETS = 2048
# Garde-fou : si la racine n'est pas celle qu'on croit, on s'arrete plutot que
# de parcourir un volume entier.
MAX_FICHIERS = 2000
BUDGET_SCAN = 5.0
BUDGET_TRANCHES = 8.0
MAX_CORPS_OCTETS = 256 * 1024


def plier(texte: str) -> str:
    """Minuscules, accents retires, espaces normalises. Du traitement de
    chaines, pas de la comprehension."""
    texte = unicodedata.normalize("NFD", texte or "")
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return " ".join(texte.lower().split())


def frontmatter(chemin: str) -> tuple[str, str]:
    """`name` et `description` depuis le frontmatter, sans analyseur YAML.

    Lit les 2 premiers kilo-octets : le frontmatter est tout en haut. Sur 177
    fichiers cela fait 350 Ko au lieu de 3,6 Mo.
    """
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
            tete = fh.read(FRONTMATTER_OCTETS)
    except OSError:
        return "", ""
    if not tete.startswith("---"):
        return "", ""
    nom = desc = ""
    for ligne in tete.splitlines()[1:]:
        depouillee = ligne.strip()
        if depouillee == "---":
            break
        if ligne[:1] in (" ", "\t", "-") or ":" not in ligne:
            continue
        cle, _, valeur = ligne.partition(":")
        valeur = valeur.strip().strip('"').strip("'")
        if cle.strip() == "name" and not nom:
            nom = valeur
        elif cle.strip() == "description" and not desc:
            desc = valeur
    return nom, desc


def lister_par_hermes() -> tuple[dict[str, str] | None, str]:
    """Les skills que Hermes considere actifs, via sa propre fonction.

    `tools.skills_tool._find_all_skills()` est « already filtered » par
    plateforme et par etat desactive : c'est la source dont Hermes batit son
    propre catalogue de prompt. S'en servir empeche le catalogue de GK de deriver
    de la realite de gb.

    Fonction privee, donc susceptible de disparaitre a une mise a jour : on
    essaie, et l'appelant retombe sur le disque. Rend `{nom: description}`.
    """
    try:
        from tools.skills_tool import _find_all_skills  # type: ignore

        items = _find_all_skills()
    except Exception as exc:
        return None, f"indisponible: {exc!r}"
    if not isinstance(items, (list, tuple)):
        return None, f"forme inattendue: {type(items)!r}"
    actifs: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        nom = str(item.get("name") or "").strip()
        if nom:
            actifs[nom] = str(item.get("description") or "")
    return (actifs or None), f"{len(actifs)} skills"


def lister_par_disque(racine: str) -> tuple[list[dict], str]:
    """Parcourt la racine et rend `{path, name, description, category,
    fingerprint}`.

    L'empreinte est `(taille, mtime_ns)` : un `stat` par fichier, aucune lecture
    du contenu. C'est ce qui permet a GK de savoir ce qui a bouge sans que le
    pont ouvre quoi que ce soit.
    """
    debut = time.monotonic()
    entrees: list[dict] = []
    tronque = False
    for dossier, sous, noms in os.walk(racine, followlinks=True):
        sous[:] = [s for s in sous if s != "__pycache__"]
        if "SKILL.md" not in noms:
            continue
        if len(entrees) >= MAX_FICHIERS or (time.monotonic() - debut) > BUDGET_SCAN:
            tronque = True
            break
        chemin = os.path.join(dossier, "SKILL.md")
        try:
            st = os.stat(chemin)
        except OSError:
            continue
        relatif = os.path.relpath(chemin, racine)
        parties = relatif.split(os.sep)
        nom, desc = frontmatter(chemin)
        entrees.append({
            "path": relatif,
            "name": nom or (parties[-2] if len(parties) >= 2 else ""),
            "description": desc,
            "category": parties[0] if len(parties) >= 2 else "",
            "fingerprint": f"{st.st_size},{st.st_mtime_ns}",
        })
    return entrees, ("tronque" if tronque else "complet")


def manifeste(racine: str | None = None) -> tuple[list[dict], bool, dict]:
    """Construit le manifeste. Rend `(entrees, complet, diagnostic)`.

    Le disque fait foi pour les chemins et les empreintes ; Hermes, quand il
    repond, fait foi pour **quels** skills sont actifs et complete les
    descriptions manquantes. `complet` passe a False si le parcours a ete
    tronque, pour que GK ne fasse disparaitre personne sur une liste partielle.
    """
    # Resolu a l'appel et non a la definition : la racine se configure, et un
    # defaut fige a l'import ne se remplace plus.
    racine = racine or RACINE_DEFAUT
    entrees, etat = lister_par_disque(racine)
    actifs, note = lister_par_hermes()
    filtres = 0
    if actifs:
        gardees = []
        for entree in entrees:
            dossier = entree["path"].split(os.sep)
            cle = entree["name"] if entree["name"] in actifs else None
            if cle is None and len(dossier) >= 2 and dossier[-2] in actifs:
                cle = dossier[-2]
            if cle is None:
                filtres += 1
                continue
            if not entree["description"]:
                entree["description"] = actifs[cle]
            gardees.append(entree)
        # Un filtre qui viderait tout est un filtre casse : on l'ignore plutot
        # que de rendre un manifeste vide, qui ferait disparaitre la
        # bibliotheque entiere cote GK.
        if gardees:
            entrees = gardees
        else:
            filtres = 0
            note += " (filtre ignore : il ne gardait rien)"
    return entrees, etat == "complet", {
        "racine": racine,
        "parcours": etat,
        "hermes": note,
        "filtres": filtres,
        "entrees": len(entrees),
    }


def cle_titre(titre: str) -> str:
    """Le mot-cle d'un titre, sans sa glose ni sa ponctuation.

    Le mot-cle s'arrete au deux-points : GM ecrit
    `## Declencheur: "commande fournisseur"`, ou la phrase de declenchement est
    DANS le titre. Sans cette coupe, le titre entier etait compare et la section
    perdue.
    """
    plie = plier((titre or "").strip("*_`#").strip())
    return plie.split(":", 1)[0].strip(" -—–.,;")


def correspond(titre: str, voulus: list[str]) -> bool:
    """Rapproche un titre d'un intitule voulu, dans les DEUX sens.

    `declencheur` doit rencontrer `declencheurs` et l'inverse : un test de
    prefixe dans un seul sens perdait le singulier, en silence, ce qui a rendu
    invisible tout un travail de redaction. La longueur minimale evite qu'un
    mot-cle trop court attrape n'importe quoi.
    """
    cle = cle_titre(titre)
    if len(cle) < 5:
        return False
    return any(cle.startswith(v) or v.startswith(cle) for v in voulus if v)


def trancher(texte: str, intitules: list[str]) -> list[dict]:
    """Decoupe un SKILL.md sur les intitules fournis par GK.

    Suit les blocs de code : un `##` dans un bloc shell est un commentaire, pas
    un titre — l'erreur frapperait precisement les skills qui portent des
    procedures. Accepte les niveaux 2 et 3, tolere une glose derriere le
    mot-cle (`## Declencheurs    quand charger / quand NE PAS charger`), le
    singulier, et une phrase placee dans le titre apres un deux-points.
    """
    voulus = [plier(i) for i in (intitules or []) if str(i or "").strip()]
    if not voulus:
        return []
    tranches: list[dict] = []
    courant: str | None = None
    corps: list[str] = []
    dans_bloc = False
    for ligne in (texte or "").splitlines():
        depouillee = ligne.strip()
        if depouillee.startswith("```") or depouillee.startswith("~~~"):
            dans_bloc = not dans_bloc
            if courant is not None:
                corps.append(ligne)
            continue
        titre = None
        if not dans_bloc and depouillee.startswith("#"):
            diese = len(depouillee) - len(depouillee.lstrip("#"))
            reste = depouillee[diese:]
            if 1 <= diese <= 6 and (reste.startswith(" ") or not reste):
                titre = reste.strip()
        if titre is not None:
            if courant is not None:
                tranches.append({"heading": courant, "text": "\n".join(corps).strip()})
                courant, corps = None, []
            if correspond(titre, voulus):
                courant, corps = titre, []
            continue
        if courant is not None:
            corps.append(ligne)
    if courant is not None:
        tranches.append({"heading": courant, "text": "\n".join(corps).strip()})
    return tranches


def tranches_pour(racine: str, chemins: list[str], intitules: list[str]) -> list[dict]:
    """Ouvre les fichiers reclames et rend leurs tranches.

    **Une entree figure meme quand aucun intitule n'a correspondu**, avec
    `sections: []`. Sans cela GK la reclamerait a chaque manifeste, pour
    toujours.

    L'empreinte est relue au moment de la lecture : si le fichier a bouge depuis
    le manifeste, GK le verra et le redemandera.
    """
    debut = time.monotonic()
    sorties: list[dict] = []
    for relatif in chemins or []:
        if (time.monotonic() - debut) > BUDGET_TRANCHES:
            break
        chemin = os.path.join(racine, relatif)
        # Ne pas sortir de la racine, meme si GK renvoyait un chemin tordu.
        if not os.path.realpath(chemin).startswith(os.path.realpath(racine) + os.sep):
            continue
        try:
            st = os.stat(chemin)
            with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
                texte = fh.read(MAX_CORPS_OCTETS)
        except OSError:
            # Disparu entre le manifeste et la lecture : une entree vide le dit,
            # et le prochain manifeste complet le marquera disparu.
            sorties.append({"path": relatif, "fingerprint": "", "sections": []})
            continue
        sorties.append({
            "path": relatif,
            "fingerprint": f"{st.st_size},{st.st_mtime_ns}",
            "sections": trancher(texte, intitules),
        })
    return sorties


# -- lecture du resultat MCP ------------------------------------------------


def extraire(resultat, noter=None) -> dict:
    """Ramene la charge utile d'un appel MCP a un dictionnaire.

    Vit ICI et non dans `__init__.py` parce que c'est le code le plus fragile du
    pont : il depend de la forme que Hermes donne a ses resultats, et cette
    forme nous a deja echappe deux fois. Place dans ce fichier, il se corrige
    par une copie, sans redemarrer l'agent.

    La forme est connue depuis le 2026-10-22
    (`hermes_cli/plugins.py`, `PluginContext._mcp_envelope`) : `call_mcp` rend
    toujours une **enveloppe**

        {"ok": True,  "result": <charge utile>}
        {"ok": True,  "result": ..., "structuredContent": ...}
        {"ok": False, "error": ...}
        {..., "truncated": True}            # au-dela de 64 Ko

    Le premier jet sondait `("wanted", "besoins", "ok", "v")` et rendait le
    dictionnaire des qu'une clef y figurait. `ok` appartient a l'enveloppe : il
    rendait donc l'enveloppe en croyant rendre la charge, `wanted` valait None,
    et la premiere ronde a envoye 182 skills, recu la reponse, n'en a rien
    compris et journalise `restants: 0` -- ce qui se lit comme un succes.
    L'enveloppe se defait AVANT toute sonde, et `ok` n'est plus une sonde.

    Consequence a ne pas perdre : une charge utile portant `error` ou `result`
    au premier niveau serait mangee par l'enveloppe. Les outils de GK nomment
    leurs champs en francais (`erreur`), ce qui les protege par accident -- et
    c'est pour cela que c'est ecrit ici.
    """
    def _dire(record: dict) -> None:
        if noter is not None:
            try:
                noter(record)
            except Exception:
                pass

    def _charge(valeur):
        if isinstance(valeur, dict):
            return valeur
        if isinstance(valeur, str) and valeur.strip().startswith("{"):
            try:
                lu = json.loads(valeur)
            except Exception:
                return None
            return lu if isinstance(lu, dict) else None
        return None

    try:
        if resultat is None:
            return {}
        contenus = None
        if isinstance(resultat, dict):
            if resultat.get("truncated"):
                _dire({"event": "resultat_tronque", "cles": sorted(resultat)})
            if resultat.get("ok") is False or "error" in resultat:
                _dire({"event": "erreur_mcp", "erreur": repr(resultat.get("error"))})
                return {}
            for cle in ("result", "structuredContent", "structured_content", "data"):
                charge = _charge(resultat.get(cle))
                if charge is not None:
                    return charge
            if any(c in resultat for c in ("wanted", "headings", "besoins", "v")):
                return resultat
            contenus = resultat.get("content")
        else:
            charge = _charge(getattr(resultat, "structuredContent", None))
            if charge is not None:
                return charge
            contenus = getattr(resultat, "content", None)
        if isinstance(contenus, (list, tuple)):
            for bloc in contenus:
                texte = (
                    bloc.get("text") if isinstance(bloc, dict)
                    else getattr(bloc, "text", None)
                )
                charge = _charge(texte)
                if charge is not None:
                    return charge
    except Exception as exc:
        _dire(_apercu(resultat, repr(exc)))
        return {}
    # Rien trouve, et il faut que ca se voie : c'est le cas qui a fait passer la
    # premiere ronde pour un succes.
    _dire(_apercu(resultat, "aucune charge utile reconnue"))
    return {}


def _apercu(resultat, raison: str) -> dict:
    record = {"event": "forme_inconnue", "type": repr(type(resultat)), "raison": raison}
    try:
        if isinstance(resultat, dict):
            record["cles"] = sorted(resultat)[:12]
        else:
            record["attributs"] = sorted(
                a for a in dir(resultat) if not a.startswith("_")
            )[:12]
    except Exception:
        pass
    return record


# -- la ronde ---------------------------------------------------------------


def ronde(ctx, noter, serveur: str = "gk", timeout: float = 30.0,
          budget: float = 25.0, racine: str | None = None) -> dict:
    """Une ronde complete : le manifeste, puis les tranches tant que GK en veut.

    Rend le resume a journaliser. L'appelant tient le verrou et la cadence ;
    cette fonction ne fait que le travail, pour qu'elle soit remplacable a
    chaud.

    Bornee par `budget` : ce qui ne passe pas cette fois passera a la ronde
    suivante, le manifeste etant auto-reparateur.
    """
    debut = time.monotonic()
    resume: dict = {"event": "ronde_catalogue"}
    try:
        entrees, complet, diag = manifeste(racine)
        resume.update(diag)
        resume["complet"] = complet
        if not entrees:
            resume["abandon"] = "manifeste vide"
            return resume
        reponse = extraire(
            ctx.call_mcp(serveur, "catalog",
                         {"skills": entrees, "complete": complet}, timeout=timeout),
            noter,
        )
        resume["manifeste_envoye"] = len(entrees)
        resume["nouveaux"] = reponse.get("nouveaux")
        resume["modifies"] = reponse.get("modifies")
        voulus = list(reponse.get("wanted") or [])
        intitules = list(reponse.get("headings") or [])
        resume["intitules"] = len(intitules)
        if voulus and not intitules:
            # GK veut des tranches mais n'a pas dit lesquelles : c'est un defaut
            # de GK, et il doit se voir plutot que de se traduire par zero
            # tranche envoyee sans explication.
            resume["anomalie"] = "wanted sans headings"
        envoyees = tours = 0
        base = diag.get("racine") or racine or RACINE_DEFAUT
        while voulus and intitules and (time.monotonic() - debut) < budget:
            items = tranches_pour(base, voulus, intitules)
            if not items:
                break
            suivant = extraire(
                ctx.call_mcp(serveur, "sections", {"items": items}, timeout=timeout),
                noter,
            )
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
    return resume
