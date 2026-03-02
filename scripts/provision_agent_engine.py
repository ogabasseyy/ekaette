"""Provision a Vertex AI Agent Engine instance for Memory Bank.

Creates an Agent Engine with:
- 90-day TTL on memories (aligned with AT call retention)
- Third-person perspective for customer service context
- Idempotent: skips creation if an engine named 'ekaette-memory' already exists

Usage:
  python -m scripts.provision_agent_engine [--project=ekaette] [--location=us-central1]

Prerequisites:
  1. gcloud services enable aiplatform.googleapis.com --project=ekaette
  2. gcloud auth application-default login

Output:
  Prints the AGENT_ENGINE_ID to set in .env
"""

from __future__ import annotations

import argparse
import os
import sys

# TTL in seconds — 90 days (matches AT_CALL_METADATA_RETENTION_DAYS)
MEMORY_TTL_SECONDS = 90 * 24 * 3600  # 7,776,000 seconds

# Engine display name used for idempotency check
ENGINE_DISPLAY_NAME = "ekaette-memory"

# Memory bank configuration following 2026 best practices
MEMORY_BANK_CONFIG = {
    "ttl_config": {
        "default_ttl": f"{MEMORY_TTL_SECONDS}s",
    },
}


def _resolve_project(args_project: str | None) -> str:
    """Resolve GCP project from arg, env, or gcloud default."""
    if args_project:
        return args_project
    env_project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if env_project:
        return env_project
    print(
        "ERROR: No project specified. Use --project, set GOOGLE_CLOUD_PROJECT, "
        "or run: gcloud config set project <project-id>",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_location(args_location: str | None) -> str:
    """Resolve GCP location from arg or env."""
    if args_location:
        return args_location
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip()
    if not location:
        location = "us-central1"
    return location


def provision(project: str, location: str, *, dry_run: bool = False) -> str:
    """Provision Agent Engine and return the engine ID.

    Returns the agent_engine_id (last segment of the resource name).
    """
    try:
        import vertexai
    except ImportError:
        print(
            "ERROR: google-cloud-aiplatform is required. "
            "Install with: pip install google-cloud-aiplatform>=1.111.0",
            file=sys.stderr,
        )
        sys.exit(1)

    client = vertexai.Client(project=project, location=location)

    # Idempotency: check if engine already exists
    print(f"Checking for existing Agent Engine '{ENGINE_DISPLAY_NAME}'...")
    try:
        existing = client.agent_engines.list()
        for engine in existing:
            resource = getattr(engine, "api_resource", None) or getattr(
                engine, "apiResource", None
            )
            if resource is None:
                continue
            display = (
                getattr(resource, "display_name", None)
                or getattr(resource, "displayName", None)
                or ""
            )
            if display == ENGINE_DISPLAY_NAME:
                resource_name = getattr(resource, "name", "") or ""
                engine_id = resource_name.split("/")[-1]
                print(f"Agent Engine already exists: {resource_name}")
                print(f"AGENT_ENGINE_ID={engine_id}")
                return engine_id
    except Exception as exc:
        print(f"Error: Could not list existing engines: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if dry_run:
        print("[DRY RUN] Would create Agent Engine with config:")
        print(f"  display_name: {ENGINE_DISPLAY_NAME}")
        print(f"  memory_bank_config: {MEMORY_BANK_CONFIG}")
        print(f"  project: {project}, location: {location}")
        return ""

    # Create new Agent Engine with Memory Bank configuration
    print(f"Creating Agent Engine '{ENGINE_DISPLAY_NAME}'...")
    print(f"  Project: {project}")
    print(f"  Location: {location}")
    print(f"  Memory TTL: {MEMORY_TTL_SECONDS // 86400} days")

    agent_engine = client.agent_engines.create(
        config={
            "displayName": ENGINE_DISPLAY_NAME,
            "contextSpec": {
                "memoryBankConfig": {
                    "ttlConfig": {
                        "defaultTtl": f"{MEMORY_TTL_SECONDS}s",
                    },
                },
            },
        },
    )

    resource = getattr(agent_engine, "api_resource", None) or getattr(
        agent_engine, "apiResource", None
    )
    resource_name = getattr(resource, "name", "") or "" if resource else ""
    engine_id = resource_name.split("/")[-1]
    print()
    print(f"Agent Engine created: {resource_name}")
    print()
    print("Add this to your .env file:")
    print(f"  AGENT_ENGINE_ID={engine_id}")
    print()
    print("Then restart the server. Memory service will auto-detect and use Vertex.")
    return engine_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision Vertex AI Agent Engine for Ekaette Memory Bank",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="GCP project ID (default: GOOGLE_CLOUD_PROJECT or 'ekaette')",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="GCP location (default: GOOGLE_CLOUD_LOCATION or 'us-central1')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without making changes",
    )
    args = parser.parse_args()

    project = _resolve_project(args.project)
    location = _resolve_location(args.location)

    provision(project, location, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
