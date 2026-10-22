"""Tests de l'outil de mesure des rubriques.

Il n'ecrit rien et ne sert que quelques fois, mais il doit etre juste : c'est
lui qui decide si la ponderation par rubrique de la phase 2 est fondee. Un
comptage faux enverrait la phase entiere dans la mauvaise direction, et le
chiffre finirait recopie dans le document qui fait foi.

Les quatre defauts du premier jet faussaient tous la mesure dans le meme sens —
faire paraitre la structure plus pauvre qu'elle n'est. Chacun a son test.
"""

from __future__ import annotations

import importlib.util
import os
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


# -- les quatre defauts corriges -------------------------------------------


def test_un_repertoire_lie_est_bien_parcouru(tmp_path):
    """`Path.rglob` ne descend pas dans un lien symbolique et se taisait : la
    bibliotheque aurait paru deux fois plus petite, sans aucune erreur."""
    vraie = tmp_path / "ailleurs"
    ecrire(vraie, "cat/cachee", SKELETTE)
    racine = tmp_path / "skills"
    racine.mkdir()
    ecrire(racine, "cat/visible", SKELETTE)
    os.symlink(vraie / "cat", racine / "liee")

    m = outil.mesurer(racine)
    assert m["fichiers_trouves"] == 2
    assert m["diagnostic_parcours"]["liens_suivis"] >= 1


def test_un_cycle_de_liens_ne_fait_pas_boucler(tmp_path):
    racine = tmp_path / "skills"
    ecrire(racine, "cat/skill", SKELETTE)
    os.symlink(racine, racine / "cat" / "retour")
    m = outil.mesurer(racine)
    assert m["fichiers_trouves"] == 1
    assert m["diagnostic_parcours"]["cycles_evites"] >= 1


def test_une_rubrique_au_niveau_trois_est_classee(tmp_path):
    """44 % des octets vivent au niveau 3 : les ignorer sous-comptait tout."""
    ecrire(tmp_path, "cat/profond", "# Titre\n### Déclencheurs\nCharger quand X.\n")
    m = outil.mesurer(tmp_path)
    assert m["rubriques"]["declencheurs"] == 1


def test_les_formes_anglaises_sont_reconnues(tmp_path):
    """`when to use` est plus frequent que `declencheurs` dans la vraie
    bibliotheque : ne chercher que du francais sous-comptait de moitie."""
    ecrire(tmp_path, "cat/en", "## When to use\nLoad this for X.\n\n## Pitfalls\nLooks like Y.\n")
    m = outil.mesurer(tmp_path)
    assert m["rubriques"]["declencheurs"] == 1
    assert m["rubriques"]["pieges"] == 1
    assert m["langue"].get("en") == 1


def test_un_prefixe_decoratif_ne_masque_pas_la_rubrique(tmp_path):
    ecrire(tmp_path, "cat/deco", "## 📚 Références\nx\n\n## 3. Procédure\ny\n\n## **Règle**\nz\n")
    m = outil.mesurer(tmp_path)
    assert m["rubriques"]["references"] == 1
    assert m["rubriques"]["procedure"] == 1
    assert m["rubriques"]["regle"] == 1


# -- comportements conserves depuis le premier jet -------------------------


def test_un_diese_dans_un_bloc_de_code_n_est_pas_un_titre(tmp_path):
    ecrire(tmp_path, "cat/complet", SKELETTE)
    m = outil.mesurer(tmp_path)
    assert m["niveaux_de_titre"] == {2: 6}
    assert m["squelette_complet"] == 1
    assert all(m["rubriques"][r] == 1 for r in outil.RUBRIQUES)


def test_un_titre_inconnu_tombe_dans_le_fourre_tout(tmp_path):
    ecrire(tmp_path, "cat/inconnu", "## Symptômes observés\nabcdefghij\n")
    m = outil.mesurer(tmp_path)
    assert m["rubriques"]["declencheurs"] == 0
    assert m["octets_par_rubrique"].get("titre_non_classe", 0) > 0
    assert m["non_classes_par_poids"][0][0] == "symptomes observes"


def test_le_preambule_et_les_niveaux_non_classes_ne_se_confondent_pas(tmp_path):
    ecrire(tmp_path, "cat/mixte", "---\nname: x\n---\npreambule\n\n#### Tres profond\ncorps\n")
    octets = outil.mesurer(tmp_path)["octets_par_rubrique"]
    assert octets.get("avant_premier_titre", 0) > 0
    assert octets.get("niveau_4", 0) > 0


def test_un_fichier_sans_titre_est_signale(tmp_path):
    ecrire(tmp_path, "cat/plat", "juste du texte, aucun titre.\n")
    m = outil.mesurer(tmp_path)
    assert m["sans_aucun_titre"] == 1


def test_les_cles_du_frontmatter_sont_relevees(tmp_path):
    ecrire(tmp_path, "cat/complet", SKELETTE)
    m = outil.mesurer(tmp_path)
    assert m["frontmatter"]["name"] == 1 and m["frontmatter"]["description"] == 1


# -- le signal negatif ------------------------------------------------------


def test_la_contre_indication_est_comptee_dedans_et_ailleurs(tmp_path):
    ecrire(tmp_path, "cat/dedans", SKELETTE)
    ecrire(tmp_path, "cat/ailleurs", "## Notes\nNe pas appliquer aux avoirs.\n")
    ecrire(tmp_path, "cat/sans", "## Déclencheurs\nCharger toujours.\n")
    m = outil.mesurer(tmp_path)
    assert m["fichiers_avec_negation_dans_declencheurs"] == 1
    # Hors rubrique reperee : un decoupage par titre la perdrait.
    assert m["fichiers_avec_negation_ailleurs"] == 1


def test_un_exemple_de_contre_indication_est_tronque(tmp_path):
    ecrire(tmp_path, "cat/dedans", SKELETTE)
    m = outil.mesurer(tmp_path, exemples=True)
    assert m["echantillons_negation"]
    assert all(len(e) <= 120 for e in m["echantillons_negation"])


# -- le refus de conclure ---------------------------------------------------


def test_un_ecart_de_denominateur_empeche_de_conclure(tmp_path, capsys):
    ecrire(tmp_path, "cat/skill", SKELETTE)
    assert outil.main(["--racine", str(tmp_path), "--attendu", "99"]) == 0
    sortie = capsys.readouterr().out
    assert "ECART" in sortie
    assert "ne rien conclure" in sortie
    # Et surtout : aucun verdict sur la ponderation.
    assert "ponderation" not in sortie


def test_un_denominateur_concordant_laisse_conclure(tmp_path, capsys):
    ecrire(tmp_path, "cat/skill", SKELETTE)
    assert outil.main(["--racine", str(tmp_path), "--attendu", "1"]) == 0
    sortie = capsys.readouterr().out
    assert "CONCORDE" in sortie
    assert "Declencheurs :" in sortie


def test_corpus_vide_et_racine_introuvable(tmp_path, capsys):
    assert outil.main(["--racine", str(tmp_path), "--json"]) == 1
    assert "aucun SKILL.md" in capsys.readouterr().err
    assert outil.main(["--racine", str(tmp_path / "nulle-part")]) == 2
    assert "introuvable" in capsys.readouterr().err
