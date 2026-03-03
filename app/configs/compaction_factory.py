"""ADK events compaction factory — env-driven configuration.

Creates an EventsCompactionConfig and wraps the root agent in an App
so the Runner can apply intra-session context compaction.

Env vars:
  COMPACTION_ENABLED      — opt-in (default "false")
  COMPACTION_INTERVAL     — events between compactions (default 5)
  COMPACTION_OVERLAP_SIZE — overlap window (default 1)
  COMPACTION_MODEL        — summarizer model ID (optional)
  COMPACTION_TOKEN_THRESHOLD — token threshold trigger (optional)
"""

import logging
import os
import warnings
from typing import Optional

from google.adk.agents.base_agent import BaseAgent
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer

from app.configs import env_flag

logger = logging.getLogger(__name__)

# Suppress experimental feature warning from ADK
warnings.filterwarnings("ignore", message=".*EXPERIMENTAL.*EventsCompactionConfig.*")

_DEFAULT_INTERVAL = 5
_DEFAULT_OVERLAP = 1

try:
    from google.adk.models import Gemini
except Exception:  # pragma: no cover
    Gemini = None  # type: ignore[assignment,misc]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r, using default %d", name, raw, default)
        return default


def _create_summarizer(model_id: str) -> Optional[LlmEventSummarizer]:
    try:
        llm = Gemini(model=model_id)
        return LlmEventSummarizer(llm=llm)
    except Exception as exc:
        logger.warning("Failed to create compaction summarizer model=%s: %s", model_id, exc)
        return None


def create_compaction_config() -> Optional[EventsCompactionConfig]:
    """Build EventsCompactionConfig from environment, or None if disabled."""
    if not env_flag("COMPACTION_ENABLED", "false"):
        return None

    interval = _env_int("COMPACTION_INTERVAL", _DEFAULT_INTERVAL)
    if interval <= 0:
        logger.warning("COMPACTION_INTERVAL=%d is non-positive, using default %d", interval, _DEFAULT_INTERVAL)
        interval = _DEFAULT_INTERVAL
    overlap = _env_int("COMPACTION_OVERLAP_SIZE", _DEFAULT_OVERLAP)
    if overlap <= 0:
        logger.warning("COMPACTION_OVERLAP_SIZE=%d is non-positive, using default %d", overlap, _DEFAULT_OVERLAP)
        overlap = _DEFAULT_OVERLAP

    model_id = os.getenv("COMPACTION_MODEL", "").strip() or None
    summarizer = _create_summarizer(model_id) if model_id else None

    token_threshold_raw = os.getenv("COMPACTION_TOKEN_THRESHOLD", "").strip()
    retention_raw = os.getenv("COMPACTION_EVENT_RETENTION_SIZE", "").strip()
    # ADK requires token_threshold and event_retention_size to be set together.
    # Use explicit None checks (not truthiness) so that configured 0 is detected as invalid.
    token_threshold = int(token_threshold_raw) if token_threshold_raw.isdigit() else None
    event_retention_size = int(retention_raw) if retention_raw.isdigit() else None
    if token_threshold is None or event_retention_size is None:
        token_threshold = None
        event_retention_size = None
    elif token_threshold <= 0 or event_retention_size <= 0:
        logger.warning(
            "Compaction paired thresholds must be positive: token_threshold=%s event_retention_size=%s — ignoring both",
            token_threshold, event_retention_size,
        )
        token_threshold = None
        event_retention_size = None

    config = EventsCompactionConfig(
        compaction_interval=interval,
        overlap_size=overlap,
        summarizer=summarizer,
        token_threshold=token_threshold,
        event_retention_size=event_retention_size,
    )
    logger.info(
        "Events compaction enabled: interval=%d overlap=%d model=%s token_threshold=%s",
        interval,
        overlap,
        model_id or "agent-default",
        token_threshold,
    )
    return config


def create_app(
    *,
    name: str,
    root_agent: BaseAgent,
) -> App:
    """Wrap the root agent in an ADK App with optional compaction config."""
    compaction_config = create_compaction_config()
    return App(
        name=name,
        root_agent=root_agent,
        events_compaction_config=compaction_config,
    )
