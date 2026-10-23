"""Adaptateur MCP. Mince par exigence de l'etape 6.

Toute la logique est dans `service.py`, qui ne connait ni transport ni
bibliotheque. Ce fichier ne fait que brancher l'un sur l'autre, pour que
changer de bibliotheque MCP reste une heure de travail et non un chantier.

Deux releves d'environnement ont corrige la conception ici :

* le SDK `mcp` 2.x a renomme `FastMCP` en `MCPServer` — ni le « FastMCP 4 » du
  handoff ni ce que l'on croyait savoir ;
* l'agent tourne en `network_mode: host`, donc aucun nom de service bridge n'est
  resoluble. On publie sur la boucle locale et le plugin vise `127.0.0.1`.

Un seul point d'entree en phase 1, `/mcp/bridge`, avec `observe` seul. Le
second (`/mcp/agent`, qui exposera `recall`, `remember` et `report` a l'agent)
viendra avec les phases suivantes : un `MCPServer` sert un chemin, donc deux
points d'entree seront deux instances montees dans la meme application.
"""

from __future__ import annotations

import logging

from .config import Config
from .service import Service

logger = logging.getLogger("gk.server")


def build(service: Service, config: Config):
    """Construit le serveur du pont. Importe le SDK tard, pour que la CLI et les
    tests n'aient pas besoin de lui."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="greenknowledge", version="0.1.0")

    @server.tool(
        name="observe",
        description=(
            "Recoit la reponse de l'agent et y cherche l'aveu d'un mauvais choix de skill. "
            "Rien n'est injecte en retour : appeler cet outil est sans effet sur l'agent."
        ),
    )
    def observe(
        session_id: str, reply: str, brief_id: int | None = None, turn_id: str = ""
    ) -> dict:
        try:
            return service.observe(session_id, reply, brief_id, turn_id)
        except Exception as exc:
            # GK se degrade, il n'echoue pas. Une observation perdue coute une
            # ligne de mesure ; une exception remontee coute un tour a l'agent.
            logger.warning("observe a echoue: %r", exc)
            return {"v": 1, "correction": False, "degrade": True, "besoins": []}

    @server.tool(
        name="request",
        description=(
            "Recoit la demande de l'utilisateur, avant l'appel au modele. "
            "OBSERVATION SEULE : rien n'est renvoye qui puisse changer le comportement "
            "de l'agent. Le texte est tronque a 200 caracteres et expire au bout de "
            "90 jours."
        ),
    )
    def request(
        session_id: str,
        text: str,
        turn_id: str = "",
        is_first_turn: bool | None = None,
        platform: str = "",
    ) -> dict:
        try:
            return service.request(session_id, text, turn_id, is_first_turn, platform)
        except Exception as exc:
            logger.warning("request a echoue: %r", exc)
            return {"v": 1, "ok": False, "degrade": True, "besoins": []}

    @server.tool(
        name="consulter",
        description=(
            "Une consultation DEMANDEE par un humain (commande `/gk`). Rend les "
            "skills qui servent la demande, avec leur description, et un "
            "identifiant de consultation pour y rattacher un retour. "
            "Contrairement a `request`, cet outil rend du contenu -- c'est le "
            "seul, et seulement parce qu'une personne l'a demande."
        ),
    )
    def consulter(demande: str, session_id: str = "", limite: int = 5) -> dict:
        try:
            return service.consulter(demande, session_id, limite)
        except Exception as exc:
            logger.warning("consulter a echoue: %r", exc)
            # Degrade sans propositions : le pont rendra None, donc l'agent
            # repond normalement et le collegue voit seulement qu'il n'a pas eu
            # d'aide.
            return {"v": 1, "ok": False, "degrade": True, "propositions": []}

    @server.tool(
        name="report",
        description=(
            "Enregistre un retour d'usage ou une notation. `source` vaut hook, agent ou human, "
            "et les trois ne se melangent jamais."
        ),
    )
    def report(
        skill: str | None = None,
        outcome: str = "unknown",
        source: str = "hook",
        session_id: str = "",
        note: str | None = None,
    ) -> dict:
        try:
            return service.report(skill, outcome, source, session_id, note)
        except Exception as exc:
            logger.warning("report a echoue: %r", exc)
            return {"v": 1, "ok": False, "degrade": True}

    @server.tool(
        name="skill_lifecycle",
        description=(
            "Enregistre un evenement de cycle de vie de skill, tel que Hermes le rapporte : "
            "un changement d'etat de l'enregistrement, pas le resultat du tour."
        ),
    )
    def skill_lifecycle(
        skill_name: str,
        action: str = "",
        session_id: str = "",
        task_id: str = "",
        use_count: int | None = None,
        turn_id: str = "",
    ) -> dict:
        try:
            return service.skill_lifecycle(
                skill_name, action, session_id, task_id, use_count, turn_id
            )
        except Exception as exc:
            logger.warning("skill_lifecycle a echoue: %r", exc)
            return {"v": 1, "ok": False, "degrade": True}

    @server.tool(
        name="catalog",
        description=(
            "Recoit le manifeste des skills et rend la liste des tranches que GK veut. "
            "Le manifeste est complet par principe, jamais des deltas : GK compare les "
            "empreintes et ne reclame que ce qui a bouge."
        ),
    )
    def catalog(skills: list[dict], complete: bool = False) -> dict:
        try:
            return service.catalog(skills, complete)
        except Exception as exc:
            logger.warning("catalog a echoue: %r", exc)
            return {"v": 1, "ok": False, "degrade": True, "wanted": [], "besoins": []}

    @server.tool(
        name="sections",
        description=(
            "Recoit les tranches des skills reclames. Une entree doit figurer meme quand "
            "aucun intitule n'a correspondu, avec sections vide, sinon GK la reclamerait "
            "indefiniment."
        ),
    )
    def sections(items: list[dict]) -> dict:
        try:
            return service.sections(items)
        except Exception as exc:
            logger.warning("sections a echoue: %r", exc)
            return {"v": 1, "ok": False, "degrade": True, "wanted": [], "besoins": []}

    @server.custom_route("/health", methods=["GET"])
    async def health(_request):
        """Teste vraiment quelque chose plutot que de repondre 200 a vide : sans
        cela, le healthcheck laisse passer un GK vivant mais casse."""
        from starlette.responses import JSONResponse

        state = service.health()
        return JSONResponse(state, status_code=200 if state.get("ok") else 503)

    return server


class BearerGuard:
    """Verifie `Authorization: Bearer <jeton>` sur le chemin MCP.

    Pourquoi ce n'est pas optionnel : la configuration portait un
    `bridge_token` et `gk check` se plaignait de son absence, alors que rien ne
    le verifiait. Un bouton qui ne fait rien est pire que pas de bouton, parce
    qu'on se croit protege.

    Ce que ce garde vaut, en revanche, est modeste et il faut le dire : la vraie
    frontiere est le binding sur la boucle locale plus le pare-feu de l'hote.
    L'agent dispose du socket Docker de l'hote, donc il peut de toute facon
    entrer dans ce conteneur et lire son environnement. C'est un garde-fou
    contre la maladresse, pas une frontiere de securite.

    `/health` reste ouvert : c'est le healthcheck du conteneur qui l'interroge.
    """

    def __init__(self, app, token: str, protege: str):
        self.app = app
        self.token = token
        self.protege = protege

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.protege):
            return await self.app(scope, receive, send)
        attendu = f"Bearer {self.token}".encode()
        fourni = dict(scope.get("headers") or {}).get(b"authorization")
        if fourni != attendu:
            from starlette.responses import JSONResponse

            return await JSONResponse(
                {"erreur": "jeton absent ou invalide"}, status_code=401
            )(scope, receive, send)
        return await self.app(scope, receive, send)


def _intitules(cfg: Config) -> tuple[str, ...] | None:
    """Les intitules de section, depuis la configuration. Vide = les defauts."""
    brut = (cfg["section_headings"] or "").strip()
    if not brut:
        return None
    return tuple(part.strip() for part in brut.split(",") if part.strip())


def run(config: Config | None = None) -> None:
    cfg = config or Config.load()
    logging.basicConfig(
        level=getattr(logging, cfg["log_level"].upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = Service.open(
        cfg.database,
        cfg.patterns,
        require_token=bool(cfg["bridge_token"]),
        headings=_intitules(cfg),
        request_max_chars=int(cfg["request_max_chars"] or 1000),
    )
    server = build(service, cfg)
    app = server.streamable_http_app(streamable_http_path=cfg["bridge_path"])

    jeton = cfg["bridge_token"]
    if jeton:
        app = BearerGuard(app, jeton, cfg["bridge_path"])
    else:
        # Bruyant a dessein : une protection absente doit se voir au demarrage,
        # pas se deviner. `gk check` et /health le disent aussi.
        logger.warning(
            "AUCUN JETON configure : tout appel sur %s est accepte. "
            "La seule protection est le binding sur %s.",
            cfg["bridge_path"], cfg["host"],
        )

    logger.info(
        "GreenKnowledge sur http://%s:%s%s — %d motifs (empreinte %s), jeton %s",
        cfg["host"], cfg.port, cfg["bridge_path"],
        len(service.detector.raw_patterns), service.detector.digest,
        "exige" if jeton else "ABSENT",
    )
    try:
        import uvicorn

        uvicorn.run(app, host=cfg["host"], port=cfg.port, log_level=cfg["log_level"].lower())
    finally:
        service.close()


if __name__ == "__main__":
    run()
