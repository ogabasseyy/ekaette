"""Provider capability probing helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Iterable

from .interfaces import ProviderCapabilities

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CapabilityProbeResult:
    """Probe output with status and capabilities."""

    ok: bool
    capabilities: ProviderCapabilities
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _split_models(raw: str, default: tuple[str, ...]) -> tuple[str, ...]:
    parts = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parts or default


def _contains_model(summaries: Iterable[dict], model_id: str) -> bool:
    for summary in summaries:
        if isinstance(summary, dict) and summary.get("modelId") == model_id:
            return True
    return False


def _list_bedrock_models(bedrock_client) -> list[dict]:
    """List Amazon foundation models with non-paginated API fallback."""
    summaries: list[dict] = []
    next_token: str | None = None
    while True:
        params: dict[str, object] = {"byProvider": "Amazon"}
        if next_token:
            params["nextToken"] = next_token
        page = bedrock_client.list_foundation_models(**params)
        models = page.get("modelSummaries", [])
        if isinstance(models, list):
            summaries.extend(item for item in models if isinstance(item, dict))
        token = page.get("nextToken")
        next_token = str(token).strip() if isinstance(token, str) else None
        if not next_token:
            break
    return summaries


def probe_provider_capabilities(
    *,
    provider: str,
    region: str,
    bedrock_client,
    voice_candidates: tuple[str, ...],
    reasoning_candidates: tuple[str, ...],
    vision_candidates: tuple[str, ...],
) -> CapabilityProbeResult:
    """Probe configured provider capabilities with conservative fallbacks."""
    if provider != "amazon_nova":
        capabilities = ProviderCapabilities(
            provider=provider,
            region=region,
            voice_model_id=None,
            reasoning_model_id=None,
            vision_model_id=None,
            voice_model_fallbacks=(),
            reasoning_model_fallbacks=(),
            supports_bidirectional_voice=False,
            supports_reasoning=False,
            supports_vision=False,
            probe_warnings=("non_nova_provider_probe_skipped",),
        )
        return CapabilityProbeResult(ok=True, capabilities=capabilities)

    summaries: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    try:
        summaries = _list_bedrock_models(bedrock_client)
    except Exception as exc:
        # Do not hard fail startup on probe fetch errors; readiness can still fail later.
        warnings.append(f"bedrock_model_listing_failed:{exc}")
        logger.warning("Bedrock model listing failed during capability probe: %s", exc)

    voice_model = next((m for m in voice_candidates if _contains_model(summaries, m)), None)
    reasoning_model = next((m for m in reasoning_candidates if _contains_model(summaries, m)), None)
    vision_model = next((m for m in vision_candidates if _contains_model(summaries, m)), None)

    if not voice_model:
        errors.append("voice_model_unavailable")
    if not reasoning_model:
        errors.append("reasoning_model_unavailable")
    if not vision_model:
        errors.append("vision_model_unavailable")

    capabilities = ProviderCapabilities(
        provider="amazon_nova",
        region=region,
        voice_model_id=voice_model,
        reasoning_model_id=reasoning_model,
        vision_model_id=vision_model,
        voice_model_fallbacks=tuple(voice_candidates[1:]) if voice_candidates else (),
        reasoning_model_fallbacks=tuple(reasoning_candidates[1:]) if reasoning_candidates else (),
        supports_bidirectional_voice=voice_model is not None,
        supports_reasoning=reasoning_model is not None,
        supports_vision=vision_model is not None,
        probe_warnings=tuple(warnings),
    )
    return CapabilityProbeResult(
        ok=not errors,
        capabilities=capabilities,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def default_model_candidates_from_env(env_getter) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Resolve default/fallback model IDs from environment."""
    voice = _split_models(
        env_getter("NOVA_VOICE_MODEL_CANDIDATES", ""),
        ("amazon.nova-2-sonic-v1:0", "amazon.nova-sonic-v1:0"),
    )
    reasoning = _split_models(
        env_getter("NOVA_REASONING_MODEL_CANDIDATES", ""),
        ("amazon.nova-2-lite-v1:0", "amazon.nova-lite-v1:0"),
    )
    vision = _split_models(
        env_getter("NOVA_VISION_MODEL_CANDIDATES", ""),
        ("amazon.nova-pro-v1:0",),
    )
    return voice, reasoning, vision
