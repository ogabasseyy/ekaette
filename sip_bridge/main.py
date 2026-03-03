"""SIP bridge entry point.

Run: python -m sip_bridge.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .config import BridgeConfig
from .server import SIPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Start the SIP bridge server."""
    config = BridgeConfig.from_env()

    # Validate config
    errors = config.validate()
    if errors:
        for err in errors:
            logger.error("Config issue: %s", err)
        logger.fatal(
            "Invalid SIP bridge configuration. Refusing to start to avoid one-way audio "
            "or failed AT registration."
        )
        sys.exit(1)

    server = SIPServer(config=config)
    await server.start()

    # Health check HTTP server (simple readiness endpoint)
    from aiohttp import web  # noqa: F401 — optional dep

    # Graceful shutdown on SIGTERM/SIGINT
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info(
        "SIP bridge running",
        extra={
            "host": config.sip_host,
            "port": config.sip_port,
            "model": config.live_model_id,
        },
    )

    await stop_event.wait()
    await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
