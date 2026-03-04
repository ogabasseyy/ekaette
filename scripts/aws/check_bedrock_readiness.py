#!/usr/bin/env python3
"""Bedrock readiness preflight for Nova deployments."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Iterable

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def _split_models(raw: str, default: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


def _contains_model(summaries: Iterable[dict], model_id: str) -> bool:
    for summary in summaries:
        if isinstance(summary, dict) and summary.get("modelId") == model_id:
            return True
    return False


def _list_bedrock_models(bedrock_client) -> list[dict]:
    """List Amazon models, handling optional nextToken without paginators."""
    summaries: list[dict] = []
    next_token: str | None = None
    while True:
        params: dict[str, object] = {"byProvider": "Amazon"}
        if next_token:
            params["nextToken"] = next_token
        response = bedrock_client.list_foundation_models(**params)
        models = response.get("modelSummaries", [])
        if isinstance(models, list):
            summaries.extend(item for item in models if isinstance(item, dict))
        token = response.get("nextToken")
        next_token = str(token).strip() if isinstance(token, str) else None
        if not next_token:
            break
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Bedrock/Nova readiness")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--voice-models", default=os.getenv("NOVA_VOICE_MODEL_CANDIDATES", ""))
    parser.add_argument(
        "--reasoning-models",
        default=os.getenv("NOVA_REASONING_MODEL_CANDIDATES", ""),
    )
    parser.add_argument("--vision-models", default=os.getenv("NOVA_VISION_MODEL_CANDIDATES", ""))
    args = parser.parse_args()

    voice_models = _split_models(
        args.voice_models, ("amazon.nova-2-sonic-v1:0", "amazon.nova-sonic-v1:0")
    )
    reasoning_models = _split_models(
        args.reasoning_models, ("amazon.nova-2-lite-v1:0", "amazon.nova-lite-v1:0")
    )
    vision_models = _split_models(args.vision_models, ("amazon.nova-pro-v1:0",))

    bedrock = boto3.client("bedrock", region_name=args.region)
    try:
        summaries = _list_bedrock_models(bedrock)
    except (BotoCoreError, ClientError) as exc:
        print(
            json.dumps(
                {
                    "region": args.region,
                    "error": "bedrock_list_foundation_models_failed",
                    "detail": str(exc),
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    report = {
        "region": args.region,
        "voice_models": {model: _contains_model(summaries, model) for model in voice_models},
        "reasoning_models": {
            model: _contains_model(summaries, model) for model in reasoning_models
        },
        "vision_models": {model: _contains_model(summaries, model) for model in vision_models},
    }

    print(json.dumps(report, indent=2))

    ok = (
        any(report["voice_models"].values())
        and any(report["reasoning_models"].values())
        and any(report["vision_models"].values())
    )
    if not ok:
        print(
            "Bedrock readiness check failed: at least one modality has no available model candidate.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
