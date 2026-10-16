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
    def observe(session_id: str, reply: str, brief_id: int | None = None) -> dict:
        try:
            return service.observe(session_id, reply, brief_id)
        except Exception as exc:
            # GK se degrade, il n'echoue pas. Une observation perdue coute une
            # ligne de mesure ; une exception remontee coute un tour a l'agent.
            logger.warning("observe a echoue: %r", exc)
            return {"v": 1, "correction": False, "degrade": True, "besoins": []}

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
    ) -> dict:
        try:
            return service.skill_lifecycle(skill_name, action, session_id, task_id, use_count)
        except Exception as exc:
            logger.warning("skill_lifecycle a echoue: %r", exc)
            return {"v": 1, "ok": False, "degrade": True}

    @server.custom_route("/health", methods=["GET"])
    async def health(_request):
        """Teste vraiment quelque chose plutot que de repondre 200 a vide : sans
        cela, le healthcheck laisse passer un GK vivant mais casse."""
        from starlette.responses import JSONResponse

        state = service.health()
        return JSONResponse(state, status_code=200 if state.get("ok") else 503)

    return server


def run(config: Config | None = None) -> None:
    cfg = config or Config.load()
    logging.basicConfig(
        level=getattr(logging, cfg["log_level"].upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = Service.open(cfg.database, cfg.patterns)
    server = build(service, cfg)
    logger.info(
        "GreenKnowledge sur http://%s:%s%s — %d motifs (empreinte %s)",
        cfg["host"], cfg.port, cfg["bridge_path"],
        len(service.detector.raw_patterns), service.detector.digest,
    )
    try:
        import anyio

        anyio.run(
            lambda: server.run_streamable_http_async(
                host=cfg["host"], port=cfg.port, streamable_http_path=cfg["bridge_path"]
            )
        )
    finally:
        service.close()


if __name__ == "__main__":
    run()
