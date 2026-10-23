"""Configuration. Lue d'un YAML minimal, surchargeable par l'environnement.

Pas de PyYAML : le fichier est plat, et une dependance de moins sur le chemin de
service est une dependance de moins a mettre a jour sur un serveur qui doit
tourner sans surveillance.

Les secrets ne vivent jamais ici en dur. Le `config.example.yaml` ne porte que
des valeurs factices, et le jeton reel arrive par l'environnement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS: dict[str, str] = {
    "database": "/var/lib/gk/gk.db",
    "patterns": "/etc/gk/motifs.yaml",
    "skills_root": "/opt/data/skills",
    # Les intitules que GK demande au pont de decouper, separes par des
    # virgules. Vide = les valeurs par defaut de `catalog.py`.
    "section_headings": "",
    # Longueur gardee d'une demande reelle. 41 % des demandes etaient coupees a
    # 200 ; 1000 couvre la quasi-totalite. L'expiration a 90 jours et
    # `gk forget` s'appliquent de la meme facon.
    "request_max_chars": "1000",
    # Bon defaut hors conteneur. Dans un conteneur, le compose impose
    # GK_HOST=0.0.0.0 et c'est la publication du port qui restreint.
    "host": "127.0.0.1",
    "port": "8765",
    "bridge_path": "/mcp/bridge",
    "agent_path": "/mcp/agent",
    "bridge_token": "",
    "agent_token": "",
    "discord_webhook": "",
    "log_level": "INFO",
}

# Variable d'environnement correspondante, qui a toujours le dernier mot.
ENV_PREFIX = "GK_"


@dataclass
class Config:
    values: dict[str, str] = field(default_factory=lambda: dict(DEFAULTS))

    def __getitem__(self, key: str) -> str:
        return self.values.get(key, "")

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    @property
    def database(self) -> Path:
        return Path(self["database"])

    @property
    def patterns(self) -> Path | None:
        p = self["patterns"]
        return Path(p) if p else None

    @property
    def port(self) -> int:
        try:
            return int(self["port"])
        except ValueError:
            return int(DEFAULTS["port"])

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        values = dict(DEFAULTS)
        candidate = Path(path) if path else Path(os.environ.get("GK_CONFIG", "/etc/gk/config.yaml"))
        if candidate.is_file():
            try:
                values.update(_read_flat_yaml(candidate.read_text(encoding="utf-8")))
            except Exception:
                # Une config illisible ne doit pas empecher GK de demarrer avec
                # ses valeurs par defaut : il se degrade, il n'echoue pas.
                pass
        for key in DEFAULTS:
            env = os.environ.get(ENV_PREFIX + key.upper())
            if env is not None:
                values[key] = env
        return cls(values)


def _read_flat_yaml(text: str) -> dict[str, str]:
    """Lit `cle: valeur` au premier niveau. Ignore commentaires et sections."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith((" ", "\t", "#")):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.split(" #")[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value:
            out[key] = value
    return out
