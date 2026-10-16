"""Detection des corrections, par motifs, en local, sans LLM.

L'etape 4 a tranche trois choses qui se tiennent :

* les motifs vivent dans un fichier **separe**, rechargeable a chaud, pour etre
  facilement modifiables ;
* on ne conserve **aucun** echantillon des reponses de l'agent, donc les motifs
  ne sont ameliorables que vers l'avant et le chiffre obtenu est un plancher,
  pas une verite ;
* ils sont donc **geles** pendant une mesure. L'empreinte du fichier est
  enregistree avec chaque correction, et `gk stats` refuse de comparer deux
  periodes dont les empreintes diffèrent.

Sans cette derniere regle, ajouter un motif au milieu d'une mesure ferait monter
le chiffre sans que rien n'ait change chez l'agent — une fausse victoire.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Motifs par defaut, en francais. Ce ne sont que des valeurs de depart : le
# fichier deploye fait foi, et c'est lui qu'on edite.
DEFAULT_PATTERNS: tuple[str, ...] = (
    r"tu as raison",
    r"vous avez raison",
    r"j'?ai utilise le mauvais",
    r"je n'?ai pas (?:pris|utilise) le bon",
    r"je n'?ai pas le bon skill",
    r"ce n'?etait pas le bon skill",
    r"j'?aurais du (?:utiliser|prendre|charger)",
    r"il (?:aurait|eut) fallu (?:utiliser|prendre)",
    r"effectivement,? je n'?ai pas",
    r"en effet,? je n'?ai pas",
    r"mea culpa",
    r"je me suis trompe",
)

# De quoi retrouver le skill nomme dans l'aveu : « j'aurais du utiliser X ».
SKILL_HINT = re.compile(
    r"(?:utiliser|prendre|charger|avec|via)\s+[`'\"]?([a-z0-9][a-z0-9_.-]{2,60})[`'\"]?",
)

MAX_EXTRACT_CHARS = 200


def fold(text: str) -> str:
    """Minuscules sans accents. Les motifs sont ecrits dans cette forme, pour
    qu'un « j'ai utilisé » et un « j'ai utilise » se detectent pareil."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class Detection:
    pattern: str
    extract: str
    skill_hint: str | None


class Detector:
    """Charge les motifs, calcule leur empreinte, cherche les aveux."""

    def __init__(self, patterns: list[str] | None = None, *, source: str = "defaults"):
        raw = list(patterns if patterns is not None else DEFAULT_PATTERNS)
        self.source = source
        self.raw_patterns: list[str] = []
        self._compiled: list[tuple[str, re.Pattern[str]]] = []
        for p in raw:
            if not isinstance(p, str) or not p.strip():
                continue
            try:
                compiled = re.compile(fold(p))
            except re.error:
                # Un motif invalide est ignore, pas fatal : le detecteur doit
                # survivre a une edition maladroite du fichier.
                continue
            self.raw_patterns.append(p)
            self._compiled.append((p, compiled))

    @property
    def digest(self) -> str:
        """Empreinte du jeu de motifs retenu. C'est elle qui interdit de
        comparer deux periodes mesurees differemment."""
        joined = "\n".join(self.raw_patterns)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    def detect(self, reply: str) -> Detection | None:
        """Premier motif qui mord, avec un extrait borne autour de lui.

        Renvoie None sur une reponse vide ou sans aveu, ce qui est le cas de la
        tres grande majorite des tours.
        """
        if not reply:
            return None
        folded = fold(reply)
        for raw, compiled in self._compiled:
            m = compiled.search(folded)
            if not m:
                continue
            # L'extrait est prelevé sur le texte d'origine, aux memes offsets :
            # le repliage preserve la longueur caractere par caractere.
            start = max(0, m.start() - MAX_EXTRACT_CHARS // 4)
            extract = reply[start : start + MAX_EXTRACT_CHARS].strip()
            hint = SKILL_HINT.search(folded[m.start() : m.start() + 200])
            # La classe de caracteres accepte le point pour les noms composes,
            # ce qui ramasse aussi la ponctuation finale d'une phrase.
            name = hint.group(1).rstrip(".-_") if hint else None
            return Detection(
                pattern=raw,
                extract=extract,
                skill_hint=name or None,
            )
        return None

    # -- chargement ---------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None) -> "Detector":
        """Charge `motifs.yaml`. Absent, illisible ou vide : on retombe sur les
        valeurs par defaut plutot que de ne rien detecter du tout."""
        if path is None:
            return cls()
        p = Path(path)
        if not p.is_file():
            return cls()
        try:
            patterns = _read_patterns(p.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        if not patterns:
            return cls()
        return cls(patterns, source=str(p))


def _read_patterns(text: str) -> list[str]:
    """Lit la liste `corrections:` d'un YAML minimal.

    On n'utilise pas PyYAML ici : le fichier est une liste de chaines sous une
    cle, et une dependance de moins sur le chemin de service est une dependance
    de moins a mettre a jour.
    """
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and stripped.endswith(":"):
            in_section = stripped[:-1].strip() == "corrections"
            continue
        if in_section and stripped.startswith("- "):
            value = stripped[2:].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                out.append(value)
    return out
