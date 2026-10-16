"""Tests de la phase 1. Ils n'ont besoin ni de Hermes ni du SDK MCP.

C'est le point du decoupage : toute la logique vit dans `service.py` et en
dessous, `server.py` n'etant qu'un adaptateur. Ce qui se teste ici est ce qui
decide quelque chose.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from gk import corrections as C  # noqa: E402
from gk.cli import main as cli_main  # noqa: E402
from gk.config import Config  # noqa: E402
from gk.patterns import Detector  # noqa: E402
from gk.service import Service  # noqa: E402
from gk.store import Store  # noqa: E402

MOTIFS = Path(__file__).resolve().parents[1] / "motifs.yaml"


@pytest.fixture
def svc(tmp_path):
    s = Service.open(tmp_path / "gk.db", MOTIFS)
    yield s
    s.close()


# -- stockage --------------------------------------------------------------


def test_wal_et_migration_idempotente(tmp_path):
    store = Store(tmp_path / "a.db")
    assert store.one("PRAGMA journal_mode")[0] == "wal"
    assert store.one("PRAGMA synchronous")[0] == 1  # NORMAL
    version = store.migrate()
    assert store.migrate() == version, "migrer deux fois ne doit rien refaire"
    store.close()


def test_reouverture_conserve_les_donnees(tmp_path):
    db = tmp_path / "b.db"
    s1 = Service.open(db, MOTIFS)
    s1.observe("sess", "Tu as raison, je n'avais pas le bon skill.")
    s1.close()
    s2 = Service.open(db, MOTIFS)
    assert s2.health()["corrections"] == 1
    s2.close()


# -- detection -------------------------------------------------------------


@pytest.mark.parametrize(
    "reponse",
    [
        "Ah oui tu as raison, je n'ai pas le bon skill.",
        "Effectivement, je n'ai pas verifie.",
        "J'aurais du utiliser deploy-history.",
        "Mea culpa, je me suis trompe.",
        "j'aurais dû utiliser perf-audit",  # avec accents
    ],
)
def test_aveux_detectes(svc, reponse):
    assert svc.observe("s", reponse)["correction"] is True


@pytest.mark.parametrize(
    "reponse",
    ["Voici le resultat : tout est vert.", "", "Le deploiement a eu lieu hier a 14h."],
)
def test_reponses_normales_ignorees(svc, reponse):
    assert svc.observe("s", reponse)["correction"] is False


def test_motif_invalide_ne_casse_pas_le_detecteur():
    d = Detector(["valide", "(((non ferme"])
    assert d.raw_patterns == ["valide"]
    assert d.detect("ceci est valide") is not None


def test_empreinte_sensible_au_jeu_de_motifs():
    assert Detector(["a", "b"]).digest == Detector(["a", "b"]).digest
    assert Detector(["a", "b"]).digest != Detector(["a", "b", "c"]).digest


def test_extrait_borne(svc):
    long = "bla " * 300 + "tu as raison " + "bla " * 300
    svc.observe("s", long)
    row = svc.store.one("SELECT extract FROM corrections ORDER BY id DESC LIMIT 1")
    assert len(row["extract"]) <= 200


# -- les trois skills ------------------------------------------------------


def test_trois_skills_distincts(svc):
    """L'aveu nomme le skill ATTENDU, pas celui utilise. Les confondre faisait
    classer une correction a l'envers."""
    svc.recorder.record_brief("s", "audit de perf", ["perf-audit", "deploy-history"])
    svc.observe("s", "Tu as raison, j'aurais du utiliser deploy-history.")
    row = svc.store.one("SELECT * FROM corrections ORDER BY id DESC LIMIT 1")
    assert row["skill_recommended"] == "perf-audit"
    assert row["skill_expected"] == "deploy-history"
    assert row["skill_used"] is None


def test_classification_mauvaise_recommandation(svc):
    svc.recorder.record_brief("s", "audit", ["perf-audit"])
    svc.observe("s", "Tu as raison.")
    out = svc.skill_lifecycle("perf-audit", action="use", session_id="s")
    assert out["attribution"] == C.BAD_RECOMMENDATION


def test_classification_brief_ignore(svc):
    svc.recorder.record_brief("s", "audit", ["perf-audit"])
    svc.observe("s", "Tu as raison.")
    out = svc.skill_lifecycle("autre-chose", action="use", session_id="s")
    assert out["attribution"] == C.BRIEF_IGNORED


def test_sans_brief_inattribuable_mais_compte(svc):
    svc.observe("s", "Mea culpa.")
    assert svc.recorder.attribute(1, None) == C.UNATTRIBUTABLE
    assert svc.store.counter("corrections_total") == 1, "le total ne mente jamais"


# -- attribution en attente ------------------------------------------------


def test_attente_fermee_a_echeance_pas_avant(svc):
    svc.observe("s", "Tu as raison.")
    assert svc.recorder.close_pending(before=time.time() - 1) == 0
    assert svc.recorder.close_pending(before=time.time() + C.PENDING_SECONDS + 1) == 1


def test_reconciliation_au_demarrage_ferme_les_orphelines(tmp_path):
    """Sans elle, un redemarrage laisse une correction en attente pour toujours,
    et elle sort des statistiques sans que personne le sache."""
    db = tmp_path / "c.db"
    s1 = Service.open(db, MOTIFS)
    s1.observe("s", "Tu as raison.")
    s1.store.execute("UPDATE corrections SET pending_until = ?", (time.time() - 10,))
    s1.close()
    s2 = Service.open(db, MOTIFS)
    row = s2.store.one("SELECT classification, pending_until FROM corrections")
    assert row["pending_until"] is None
    assert row["classification"] != "pending"
    s2.close()


# -- retention a trois niveaux --------------------------------------------


def test_la_preuve_expire_la_mesure_reste(svc):
    svc.recorder.record_brief("s", "audit", ["perf-audit"])
    svc.observe("s", "Tu as raison.")
    svc.skill_lifecycle("perf-audit", session_id="s")
    svc.store.execute("UPDATE corrections SET extract_expires_at = ?", (time.time() - 1,))
    assert C.tidy(svc.store)["preuves_effacees"] == 1
    row = svc.store.one("SELECT extract, classification, patterns_digest FROM corrections")
    assert row["extract"] is None
    assert row["classification"] == C.BAD_RECOMMENDATION
    assert row["patterns_digest"]


def test_cas_de_test_lie_a_l_etiquette_seule(svc):
    """Une recommandation disparue n'invalide pas la lecon « pour cette demande,
    le bon skill etait X »."""
    svc.recorder.record_brief("s", "audit", ["perf-audit"])
    svc.observe("s", "Tu as raison, j'aurais du utiliser deploy-history.")
    assert C.retire_testcases(svc.store, {"deploy-history"}) == 0
    assert C.retire_testcases(svc.store, {"perf-audit"}) == 1
    row = svc.store.one("SELECT request_truncated, classification FROM corrections")
    assert row["request_truncated"] is None


def test_agregat_usages_survit_a_la_purge(svc):
    for _ in range(3):
        svc.skill_lifecycle("perf-audit", action="use")
    svc.store.execute("UPDATE skill_uses SET expires_at = ?", (time.time() - 1,))
    C.tidy(svc.store)
    assert svc.store.one("SELECT uses FROM skill_use_totals")["uses"] == 3


def test_briefs_purges_a_sept_jours(svc):
    svc.recorder.record_brief("s", "demande", ["x"])
    svc.store.execute("UPDATE briefs SET expires_at = ?", (time.time() - 1,))
    assert C.tidy(svc.store)["briefs_purges"] == 1


# -- sources de report -----------------------------------------------------


def test_les_trois_sources_restent_distinctes(svc):
    for source in ("hook", "agent", "human"):
        assert svc.report("perf-audit", "ko", source)["ok"] is True
    rows = svc.store.query("SELECT source, COUNT(*) n FROM reports GROUP BY source")
    assert {r["source"]: r["n"] for r in rows} == {"hook": 1, "agent": 1, "human": 1}


def test_source_inconnue_refusee(svc):
    assert svc.report("x", "ok", "n'importe quoi")["ok"] is False


# -- degradation -----------------------------------------------------------


def test_observe_ne_leve_jamais_sur_une_entree_absurde(svc):
    for mauvais in (None, 12, {"a": 1}, b"octets"):
        out = svc.observe("s", mauvais)  # type: ignore[arg-type]
        assert out["correction"] is False


def test_health_teste_vraiment_quelque_chose(svc):
    h = svc.health()
    assert h["ok"] is True
    assert h["patterns"] > 0 and h["patterns_digest"]


# -- CLI -------------------------------------------------------------------


def test_stats_refuse_de_comparer_deux_jeux_de_motifs(svc, capsys, monkeypatch):
    svc.observe("s", "Tu as raison.")
    svc.detector = Detector(["un motif tout autre"])
    svc.observe("s", "un motif tout autre ici")
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(svc.store.path))
    assert cli_main(["--json", "stats"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["periodes"]) == 2
    assert out["comparaison"].startswith("REFUSEE")


def test_forget_efface_le_texte_et_garde_la_mesure(svc, capsys, monkeypatch):
    svc.recorder.record_brief("s", "audit", ["perf-audit"])
    svc.observe("s", "Tu as raison.")
    svc.skill_lifecycle("perf-audit", session_id="s")
    path = svc.store.path
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(path))
    assert cli_main(["--json", "forget", "--extracts"]) == 0
    assert json.loads(capsys.readouterr().out)["preuves_effacees"] == 1
    store = Store(path)
    row = store.one("SELECT extract, classification FROM corrections")
    assert row["extract"] is None and row["classification"] == C.BAD_RECOMMENDATION
    store.close()


def test_export_ne_sort_que_les_quatre_tables(svc, tmp_path, capsys, monkeypatch):
    svc.observe("s", "Tu as raison.")
    path = svc.store.path
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(path))
    cible = tmp_path / "dump.json"
    assert cli_main(["--json", "export", "-o", str(cible)]) == 0
    dump = json.loads(cible.read_text(encoding="utf-8"))
    assert set(dump["tables"]) == {"corrections", "reports", "skill_use_totals", "counters"}
    assert "briefs" not in dump["tables"], "le journal des briefs se reconstruit, pas la mesure"


def test_check_signale_un_pont_jamais_installe(svc, capsys, monkeypatch):
    path = svc.store.path
    svc.close()
    monkeypatch.setenv("GK_DATABASE", str(path))
    monkeypatch.setenv("GK_BRIDGE_TOKEN", "jeton")
    assert cli_main(["--json", "check"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert any("jamais" in p for p in out["problemes"])


# -- config ----------------------------------------------------------------


def test_env_a_le_dernier_mot(tmp_path, monkeypatch):
    f = tmp_path / "config.yaml"
    f.write_text("database: /du/fichier\nport: 9999\n", encoding="utf-8")
    monkeypatch.setenv("GK_DATABASE", "/de/l/env")
    cfg = Config.load(f)
    assert str(cfg.database) == "/de/l/env"
    assert cfg.port == 9999


def test_config_illisible_ne_bloque_pas_le_demarrage(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("\x00\x01 pas du yaml", encoding="utf-8")
    assert Config.load(f).port == 8765
