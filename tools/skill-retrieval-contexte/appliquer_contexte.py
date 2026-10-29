#!/usr/bin/env python3
"""Applique le repli de contexte a `skill-retrieval`, sans numeros de ligne.

Usage :  python3 appliquer_contexte.py [--verifier] /chemin/du/plugin/skill-retrieval

`--verifier` n'ecrit rien : il dit seulement si la version installee porte les
ancres, et expose ce qu'il a trouve. A lancer d'abord quand la version installee
ne vient pas du depot public -- le catalogue en distribue une 0.6.0 qui n'y
existe pas.

Ancre sur deux lignes qui existent en 0.5 comme en 0.6 : l'import de
`bm25_retriever` et l'appel a `_apply_relevance_floor` dans le hook. Rien n'est
ecrit si une ancre manque ou si le correctif est deja la : un applicateur qui
patche a moitie est pire qu'un applicateur qui refuse.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ANCRE_IMPORT = "from bm25_retriever import clear_index_cache, get_index, get_skill_info"
IMPORT_AJOUTE = "from context_boost import augmented_query, needs_context"

ANCRE_HOOK = """        results = index.retrieve(user_message, top_k=TOP_K)
        results = _apply_relevance_floor(results, MIN_SCORE_RATIO)
"""

BLOC = """        results = index.retrieve(user_message, top_k=TOP_K)
        results = _apply_relevance_floor(results, MIN_SCORE_RATIO)
        # --- local patch: conversation context as a fallback query ----------
        # Only when the question alone decided nothing. See context_boost.py
        # for why this is a fallback and not a permanent query expansion.
        if needs_context(user_message, results):
            query = augmented_query(user_message, kwargs)
            if query:
                fallback = _apply_relevance_floor(
                    index.retrieve(query, top_k=TOP_K), MIN_SCORE_RATIO
                )
                if fallback:
                    logger.info(
                        "Context fallback used for session %s (%d -> %d results)",
                        session_id, len(results), len(fallback),
                    )
                    results = fallback
        # --- end local patch -------------------------------------------------
"""


def _rapport(racine: Path, cible: Path, texte: str) -> int:
    """Dit ce qui a ete trouve, et n'ecrit rien.

    Separe de l'application parce qu'une version qu'on ne peut pas lire depuis
    le depot public doit pouvoir etre sondee avant d'etre touchee.
    """
    import hashlib

    version = "?"
    manifeste = racine / "plugin.yaml"
    if manifeste.is_file():
        for ligne in manifeste.read_text(encoding="utf-8").splitlines():
            if ligne.strip().lower().startswith("version"):
                version = ligne.split(":", 1)[-1].strip().strip("\"'")
    print(f"plugin   : {racine}")
    print(f"version  : {version}")
    print(f"__init__ : {len(texte.splitlines())} lignes, "
          f"sha256 {hashlib.sha256(texte.encode()).hexdigest()[:16]}")
    deja = IMPORT_AJOUTE in texte and "end local patch" in texte
    print(f"patche   : {'OUI' if deja else 'non'}")
    manques = []
    for ancre, nom in ((ANCRE_IMPORT, "import de bm25_retriever"),
                       (ANCRE_HOOK, "appel a _apply_relevance_floor")):
        n = texte.count(ancre)
        print(f"ancre    : {nom} -> {n} occurrence(s) {'OK' if n == 1 else 'PROBLEME'}")
        if n != 1:
            manques.append(nom)
    # Ce que le hook recoit vraiment, utile meme quand une ancre manque.
    for motif in ("def _on_pre_llm_call", "index.retrieve(", "_apply_relevance_floor("):
        lignes = [str(i + 1) for i, l in enumerate(texte.splitlines()) if motif in l]
        print(f"presence : {motif!r} aux lignes {', '.join(lignes) or '(aucune)'}")
    if manques:
        print("\nVERDICT : ancres absentes -> l'application REFUSERA.")
        print("          Envoyez ce rapport et le __init__.py, les ancres seront refaites.")
        return 1
    print("\nVERDICT : applicable." if not deja else "\nVERDICT : deja applique.")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    verifier = "--verifier" in argv[1:]
    if len(args) != 1:
        print(__doc__.strip())
        return 2
    racine = Path(args[0]).expanduser().resolve()
    cible = racine / "__init__.py"
    module = racine / "scripts" / "context_boost.py"
    source = Path(__file__).resolve().parent / "context_boost.py"

    if not cible.is_file():
        print(f"REFUS : {cible} est introuvable — mauvais chemin de plugin ?")
        return 1
    if not (racine / "scripts" / "bm25_retriever.py").is_file():
        print(f"REFUS : {racine}/scripts/bm25_retriever.py est introuvable")
        return 1
    if not source.is_file() and not verifier:
        print(f"REFUS : {source} doit etre a cote de ce script")
        return 1

    texte = cible.read_text(encoding="utf-8")
    if verifier:
        return _rapport(racine, cible, texte)
    if IMPORT_AJOUTE in texte and "end local patch" in texte:
        print("DEJA APPLIQUE : rien a faire.")
        return 0
    for ancre, nom in ((ANCRE_IMPORT, "l'import de bm25_retriever"),
                       (ANCRE_HOOK, "l'appel a _apply_relevance_floor")):
        if texte.count(ancre) != 1:
            print(f"REFUS : {nom} apparait {texte.count(ancre)} fois, attendu 1.")
            print("        La version installee differe — envoyez-moi son __init__.py.")
            return 1

    sauvegarde = cible.with_suffix(f".py.avant-contexte-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(cible, sauvegarde)
    shutil.copy2(source, module)
    cible.write_text(
        texte.replace(ANCRE_IMPORT, f"{ANCRE_IMPORT}\n{IMPORT_AJOUTE}")
             .replace(ANCRE_HOOK, BLOC),
        encoding="utf-8",
    )
    print(f"OK  sauvegarde : {sauvegarde}")
    print(f"OK  module     : {module}")
    print(f"OK  patche     : {cible}")
    print("\nRedemarrez la session Hermes, puis cherchez « Context fallback used » dans ses logs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
