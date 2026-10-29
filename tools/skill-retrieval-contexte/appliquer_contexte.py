#!/usr/bin/env python3
"""Applique le repli de contexte a `skill-retrieval`, sans numeros de ligne.

Usage :  python3 appliquer_contexte.py /chemin/du/plugin/skill-retrieval

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


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    racine = Path(argv[1]).expanduser().resolve()
    cible = racine / "__init__.py"
    module = racine / "scripts" / "context_boost.py"
    source = Path(__file__).resolve().parent / "context_boost.py"

    if not cible.is_file():
        print(f"REFUS : {cible} est introuvable — mauvais chemin de plugin ?")
        return 1
    if not (racine / "scripts" / "bm25_retriever.py").is_file():
        print(f"REFUS : {racine}/scripts/bm25_retriever.py est introuvable")
        return 1
    if not source.is_file():
        print(f"REFUS : {source} doit etre a cote de ce script")
        return 1

    texte = cible.read_text(encoding="utf-8")
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
