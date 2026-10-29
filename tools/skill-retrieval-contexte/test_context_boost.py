"""Conversation context as a fallback query (local patch).

The property that matters most is the *negative* one: when the question alone
already retrieves something, the patch must not touch the query. A regression
there would change every turn silently, which is exactly what makes query
expansion dangerous on a de-duplicating BM25 with a relative floor.
"""

import importlib
import importlib.util
from pathlib import Path

import pytest

import context_boost as cb

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location(
        "skill_retrieval_plugin_ctx", PLUGIN_DIR / "__init__.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FauxIndex:
    """Rend un resultat seulement si la requete porte le mot 'index'."""

    def __init__(self):
        self.requetes = []

    def retrieve(self, query, top_k=5):
        self.requetes.append(query)
        if "index" in query.lower():
            return [("gm-index-rebuild", 4.0)]
        return []


def _hook_pret(monkeypatch, index):
    mod = _load_plugin_module()
    monkeypatch.setattr(mod, "get_index", lambda **kw: index)
    monkeypatch.setattr(mod, "_capability_kwargs_for_session", lambda s: {})
    monkeypatch.setattr(
        mod, "get_skill_info",
        lambda skill_id, **kw: {"name": skill_id, "description": "desc"},
    )
    return mod


# --- Forme de l'historique ---------------------------------------------------

@pytest.mark.parametrize("historique", [
    [{"role": "user", "content": "reconstruire l'index produits"}],
    [{"role": "user", "text": "reconstruire l'index produits"}],
    ["reconstruire l'index produits"],
    [{"role": "user", "content": [{"type": "text", "text": "reconstruire l'index produits"}]}],
])
def test_le_contexte_se_lit_quelle_que_soit_la_forme(historique):
    assert "index" in cb.build_context({"conversation_history": historique})


def test_un_objet_plutot_qu_un_dictionnaire_est_lu_aussi():
    class Tour:
        content = "reconstruire l'index produits"
    assert "index" in cb.build_context({"conversation_history": [Tour()]})


def test_sans_historique_le_contexte_est_vide():
    assert cb.build_context({}) == ""
    assert cb.build_context({"conversation_history": []}) == ""
    assert cb.augmented_query("go", {}) == ""


def test_seuls_les_derniers_tours_sont_lus():
    historique = [{"content": f"tour {i}"} for i in range(10)]
    contexte = cb.build_context({"conversation_history": historique}, turns=2)
    assert "tour 9" in contexte and "tour 8" in contexte and "tour 7" not in contexte


def test_chaque_tour_est_tronque():
    historique = [{"content": "x" * 5000}]
    assert len(cb.build_context({"conversation_history": historique}, chars=100)) == 100


def test_la_question_reste_en_tete_de_la_requete():
    requete = cb.augmented_query("go", {"conversation_history": [{"content": "l'index"}]})
    assert requete.startswith("go\n")


# --- Quand le repli se declenche --------------------------------------------

def test_resultat_vide_declenche_le_repli():
    assert cb.needs_context("controle des factures fournisseurs", []) is True


def test_question_pauvre_declenche_le_repli_malgre_un_resultat():
    assert cb.needs_context("go", [("x", 1.0)]) is True


def test_question_riche_avec_resultat_ne_declenche_rien():
    assert cb.needs_context(
        "controle des factures fournisseurs proforma", [("x", 1.0)]
    ) is False


def test_desactive_par_l_environnement(monkeypatch):
    monkeypatch.setenv("SKILL_RETRIEVAL_CONTEXT", "0")
    eteint = importlib.reload(cb)
    try:
        assert eteint.needs_context("go", []) is False
        assert eteint.augmented_query("go", {"conversation_history": [{"content": "x"}]}) == ""
    finally:
        monkeypatch.delenv("SKILL_RETRIEVAL_CONTEXT")
        importlib.reload(cb)


# --- Le hook, de bout en bout ------------------------------------------------

def test_le_hook_rattrape_une_question_pauvre_grace_au_contexte(monkeypatch):
    index = FauxIndex()
    mod = _hook_pret(monkeypatch, index)
    sortie = mod._on_pre_llm_call(
        session_id="s1",
        user_message="etape 1 validee, poursuis",
        conversation_history=[
            {"role": "user", "content": "il faut reconstruire l'index produits"},
            {"role": "assistant", "content": "L'import est fini, il reste l'index."},
        ],
    )
    assert sortie is not None and "gm-index-rebuild" in sortie["context"]
    assert len(index.requetes) == 2, "la premiere recherche doit avoir eu lieu seule"
    assert index.requetes[0] == "etape 1 validee, poursuis"
    assert "index" in index.requetes[1]


def test_le_hook_ne_touche_pas_a_la_requete_quand_la_question_suffit(monkeypatch):
    """La propriete de surete : aucun changement sur les tours qui marchaient."""
    index = FauxIndex()
    mod = _hook_pret(monkeypatch, index)
    sortie = mod._on_pre_llm_call(
        session_id="s1",
        user_message="reconstruire l'index produits prestashop maintenant",
        conversation_history=[{"role": "user", "content": "factures fournisseurs proforma"}],
    )
    assert sortie is not None
    assert index.requetes == ["reconstruire l'index produits prestashop maintenant"]


def test_sans_historique_le_hook_garde_son_resultat_vide(monkeypatch):
    index = FauxIndex()
    mod = _hook_pret(monkeypatch, index)
    assert mod._on_pre_llm_call(session_id="s1", user_message="controle des factures") is None
    assert index.requetes == ["controle des factures"]


def test_un_historique_hors_sujet_ne_fabrique_pas_de_resultat(monkeypatch):
    index = FauxIndex()
    mod = _hook_pret(monkeypatch, index)
    sortie = mod._on_pre_llm_call(
        session_id="s1", user_message="poursuis",
        conversation_history=[{"role": "user", "content": "bonjour, comment vas-tu"}],
    )
    assert sortie is None
    assert len(index.requetes) == 2
