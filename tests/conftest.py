"""Isole la suite de l'environnement de celui qui la lance.

`Config.load` donne le dernier mot aux variables `GK_*`, ce qui est voulu en
production et nuisible en test : un `GK_DATABASE` exporte dans le shell fait
echouer les tests qui passent un `--config`, et l'echec accuse alors le code au
lieu du shell. Les variables sont donc retirees pour toute la suite.

Autouse plutot qu'a la demande : le piege ne se voit pas depuis le test qui
tombe, donc il ne faut pas avoir a y penser.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def environnement_neutre(monkeypatch):
    for nom in [n for n in os.environ if n.startswith("GK_")]:
        monkeypatch.delenv(nom, raising=False)
