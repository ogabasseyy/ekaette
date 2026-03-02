"""Memory service factory — returns VertexAi or InMemory based on config."""

import logging
import os

from google.adk.memory import InMemoryMemoryService
from google.adk.memory.base_memory_service import BaseMemoryService

logger = logging.getLogger(__name__)


def _try_vertex_memory_service(
    project: str,
    location: str,
    agent_engine_id: str,
) -> BaseMemoryService | None:
    """Attempt to create VertexAiMemoryBankService, return None on failure."""
    try:
        from google.adk.memory import VertexAiMemoryBankService

        return VertexAiMemoryBankService(
            project=project,
            location=location,
            agent_engine_id=agent_engine_id,
        )
    except Exception as exc:
        logger.warning("VertexAiMemoryBankService init failed: %s", exc)
        return None


def create_memory_service() -> BaseMemoryService:
    """Create memory service based on environment configuration.

    Backends:
      - MEMORY_BACKEND=memory: always use InMemoryMemoryService.
      - MEMORY_BACKEND=vertex: require AGENT_ENGINE_ID, then try Vertex.
      - MEMORY_BACKEND=auto (default): use Vertex when AGENT_ENGINE_ID exists.

    Falls back to InMemoryMemoryService on missing config or init failure.
    """
    backend = os.getenv("MEMORY_BACKEND", "auto").strip().lower()

    if backend in {"memory", "inmemory"}:
        logger.info("Memory service: InMemoryMemoryService (MEMORY_BACKEND=%s)", backend)
        return InMemoryMemoryService()

    if backend not in {"auto", "vertex", ""}:
        logger.warning(
            "Unknown MEMORY_BACKEND '%s' — defaulting to auto mode",
            backend,
        )
        backend = "auto"

    if backend in {"auto", "vertex", ""}:
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip()
        agent_engine_id = os.getenv("AGENT_ENGINE_ID", "").strip()

        if not agent_engine_id:
            if backend == "vertex":
                raise RuntimeError(
                    "MEMORY_BACKEND=vertex requires AGENT_ENGINE_ID to be set."
                )
            return InMemoryMemoryService()

        service = _try_vertex_memory_service(project, location, agent_engine_id)
        if service is not None:
            logger.info(
                "Memory service: VertexAiMemoryBankService "
                "(project=%s, engine=%s)",
                project,
                agent_engine_id,
            )
            return service

        if backend == "vertex":
            raise RuntimeError(
                "MEMORY_BACKEND=vertex but VertexAiMemoryBankService init failed. "
                "Check AGENT_ENGINE_ID and credentials."
            )
        logger.warning(
            "VertexAiMemoryBankService failed — falling back to InMemoryMemoryService"
        )

    return InMemoryMemoryService()
