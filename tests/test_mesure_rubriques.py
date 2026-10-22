"""Tests de l'outil de mesure des rubriques.

Il n'ecrit rien et ne sert qu'une fois, mais il doit etre juste : c'est lui qui
decide si la ponderation par rubrique de la phase 2 est fondee ou fantaisiste.
Un comptage faux enverrait toute la phase dans la mauvaise direction, et le
chiffre serait recopie dans le document qui fait foi.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "mesurer_rubriques", RACINE / "tools" / "mesurer-rubriques.py"
)
assert spec and spec.loader
outil = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outil)


SKELETTE = """---
name: domaine-sujet
description: "declencheur concret — contenu"
---
## Déclencheurs
Charger pour une commande fournisseur.
Ne pas charger pour une commande client.

## Règle
Valider le fournisseur.

## Procédure
```bash
# commentaire
## pas un titre non plus
./bin/faire
```

## Pièges
Ressemble a X.

## Vérification
Preuve mesurable.

## Références
references/2026-01.md
"""


def ecrire(racine: Path, chemin: str, texte: str) -> None:
    cible = racine / chemin / "SKILL.md"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(texte, encoding="utf-8")


def test_un_diese_dans_un_bloc_de_code_n_est_pas_un_titre(tmp_path):
    ecrire(tmp_path, "cat/complet", SKELETTE)
    m = outil.mesurer(tmp_path, exemples=False)
    # Six rubriques, et aucune des deux lignes du bloc shell.
    assert m["niveaux_de_titre"] == {2: 6}
    assert m["squelette_complet"] == 1
    assert all(m["rubriques"][r] == 1 for r in outil.RUBRIQUES)


def test_la_contre_indication_est_reperee_dans_declencheurs(tmp_path):
    ecrire(tmp_path, "cat/complet", SKELETTE)
    ecrire(tmp_path, "cat/sans-negation", "## Déclencheurs\nCharger toujours.\n")
    m = outil.mesurer(tmp_path, exemples=False)
    assert m["declencheurs_sections"] == 2
    assert m["declencheurs_avec_negation"] == 1


def test_les_variantes_de_titre_sont_classees(tmp_path):
    ecrire(tmp_path, "cat/variante", "## Quand utiliser\nx\n\n## Procédure de diagnostic\ny\n")
    m = outil.mesurer(tmp_path, exemples=False)
    assert m["rubriques"]["declencheurs"] == 1
    assert m["rubriques"]["procedure"] == 1
    assert m["squelette_complet"] == 0


def test_un_titre_inconnu_tombe_dans_le_fourre_tout(tmp_path):
    ecrire(tmp_path, "cat/inconnu", "## Symptômes observés\nabcdefghij\n")
    m = outil.mesurer(tmp_path, exemples=False)
    assert m["rubriques"]["declencheurs"] == 0
    assert m["octets_par_rubrique"].get("titre_non_standard", 0) > 0
    assert "symptomes observes" in dict(m["titres_frequents"])


def test_un_fichier_sans_titre_est_signale(tmp_path):
    ecrire(tmp_path, "cat/plat", "juste du texte, aucun titre.\n")
    m = outil.mesurer(tmp_path, exemples=False)
    assert m["sans_aucun_titre"] == 1
    assert m["octets_par_rubrique"].get("avant_premier_titre", 0) > 0


def test_le_frontmatter_et_le_preambule_ne_se_confondent_pas_avec_un_autre_niveau(tmp_path):
    ecrire(tmp_path, "cat/mixte", "---\nname: x\n---\npreambule\n\n### Sous-titre\ncorps\n")
    m = outil.mesurer(tmp_path, exemples=False)
    octets = m["octets_par_rubrique"]
    assert octets.get("avant_premier_titre", 0) > 0
    assert octets.get("autre_niveau", 0) > 0
    assert m["niveaux_de_titre"] == {3: 1}


def test_les_cles_du_frontmatter_sont_relevees(tmp_path):
    ecrire(tmp_path, "cat/complet", SKELETTE)
    m = outil.mesurer(tmp_path, exemples=False)
    assert m["frontmatter"]["name"] == 1
    assert m["frontmatter"]["description"] == 1


def test_sortie_json_sur_un_corpus_vide(tmp_path, capsys):
    assert outil.main(["--racine", str(tmp_path), "--json"]) == 1
    assert "aucun SKILL.md" in capsys.readouterr().err


def test_racine_introuvable(tmp_path, capsys):
    assert outil.main(["--racine", str(tmp_path / "nulle-part")]) == 2
    assert "introuvable" in capsys.readouterr().err
