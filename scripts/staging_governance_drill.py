"""End-to-end staging governance drill: export -> retention purge -> delete -> restore -> verify."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

from scripts.dr_restore_drill import SUBCOLLECTIONS, restore_company_snapshot

RequestJsonFn = Callable[[str, str, dict[str, str], dict[str, Any] | None, float], dict[str, Any]]


def _request_json(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    data: bytes | None = None
    request_headers = dict(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url=url, method=method.upper(), data=data, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8").strip()
            if not raw:
                return {}
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise RuntimeError(f"{method} {url} returned non-object payload")
            return parsed
    except urllib.error.HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace").strip()
        error_details = raw_error
        try:
            parsed_error = json.loads(raw_error) if raw_error else {}
            if isinstance(parsed_error, dict):
                error_details = parsed_error.get("error") or parsed_error.get("code") or raw_error
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"{method} {url} failed with status={exc.code}: {error_details}") from exc


def _normalize_collections(raw_collections: Sequence[str]) -> list[str]:
    normalized = [str(item).strip().lower() for item in raw_collections if str(item).strip()]
    if not normalized:
        return list(SUBCOLLECTIONS)
    deduped: list[str] = []
    for name in normalized:
        if name not in deduped:
            deduped.append(name)
    allowed = set(SUBCOLLECTIONS)
    invalid = sorted(set(deduped) - allowed)
    if invalid:
        raise ValueError(f"unsupported collection(s): {', '.join(invalid)}")
    return deduped


def _build_admin_headers(*, tenant_id: str, user_id: str, roles_csv: str, scopes_csv: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant_id,
        "X-User-Id": user_id,
        "X-Roles": roles_csv,
        "X-Scopes": scopes_csv,
    }


def _build_export_snapshot(*, export_payload: dict[str, Any], tenant_id: str, company_id: str) -> dict[str, Any]:
    company = export_payload.get("company")
    collections = export_payload.get("collections")
    if not isinstance(company, dict) or not isinstance(collections, dict):
        raise ValueError("export payload missing company/collections object")

    snapshot_collections: dict[str, list[dict[str, Any]]] = {}
    for collection_name in SUBCOLLECTIONS:
        raw_entries = collections.get(collection_name, [])
        if not isinstance(raw_entries, list):
            raw_entries = []
        normalized_entries: list[dict[str, Any]] = []
        for entry in raw_entries:
            if isinstance(entry, dict):
                normalized_entries.append(dict(entry))
        snapshot_collections[collection_name] = normalized_entries

    return {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "company_doc": dict(company),
        "collections": snapshot_collections,
    }


def run_staging_governance_drill(
    *,
    base_url: str,
    tenant_id: str,
    company_id: str,
    user_id: str,
    roles_csv: str,
    scopes_csv: str,
    older_than_days: int,
    collections: Sequence[str],
    data_tier: str | None,
    output_path: Path | None,
    project_id: str,
    dry_run: bool = False,
    timeout_seconds: float = 20.0,
    request_json_fn: RequestJsonFn = _request_json,
    db: Any | None = None,
) -> dict[str, Any]:
    normalized_base_url = base_url.rstrip("/")
    encoded_company_id = urllib.parse.quote(company_id, safe="")
    query = urllib.parse.urlencode({"tenantId": tenant_id})

    export_url = f"{normalized_base_url}/api/v1/admin/companies/{encoded_company_id}/export?{query}"
    purge_url = f"{normalized_base_url}/api/v1/admin/companies/{encoded_company_id}/retention/purge?{query}"
    delete_url = f"{normalized_base_url}/api/v1/admin/companies/{encoded_company_id}?{query}"

    normalized_collections = _normalize_collections(collections)
    headers = _build_admin_headers(
        tenant_id=tenant_id,
        user_id=user_id,
        roles_csv=roles_csv,
        scopes_csv=scopes_csv,
    )

    export_before = request_json_fn(
        "POST",
        export_url,
        headers,
        {"includeRuntimeData": True},
        timeout_seconds,
    )
    snapshot = _build_export_snapshot(
        export_payload=export_before,
        tenant_id=tenant_id,
        company_id=company_id,
    )

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    if dry_run:
        return {
            "success": True,
            "dryRun": True,
            "tenantId": tenant_id,
            "companyId": company_id,
            "snapshotCounts": export_before.get("counts", {}),
            "outputPath": str(output_path) if output_path else None,
        }

    purge_headers = dict(headers)
    purge_headers["Idempotency-Key"] = f"purge-{uuid4().hex}"
    purge_result = request_json_fn(
        "POST",
        purge_url,
        purge_headers,
        {
            "olderThanDays": int(older_than_days),
            "collections": normalized_collections,
            "dataTier": data_tier.strip().lower() if isinstance(data_tier, str) and data_tier.strip() else None,
        },
        timeout_seconds,
    )

    delete_headers = dict(headers)
    delete_headers["Idempotency-Key"] = f"delete-{uuid4().hex}"
    delete_result = request_json_fn(
        "DELETE",
        delete_url,
        delete_headers,
        None,
        timeout_seconds,
    )

    if db is None:
        from google.cloud import firestore

        db = firestore.Client(project=project_id)

    restored = restore_company_snapshot(db, snapshot)
    export_after = request_json_fn(
        "POST",
        export_url,
        headers,
        {"includeRuntimeData": True},
        timeout_seconds,
    )

    snapshot_counts = export_before.get("counts", {})
    verify_counts = export_after.get("counts", {})
    counts_match = snapshot_counts == verify_counts

    # Validate intermediate step results
    purge_ok = isinstance(purge_result, dict) and purge_result.get("success", purge_result.get("ok", True)) is not False
    delete_ok = isinstance(delete_result, dict) and delete_result.get("success", delete_result.get("ok", True)) is not False
    all_steps_ok = counts_match and purge_ok and delete_ok

    return {
        "success": bool(all_steps_ok),
        "dryRun": False,
        "tenantId": tenant_id,
        "companyId": company_id,
        "snapshotCounts": snapshot_counts,
        "verifyCounts": verify_counts,
        "countsMatch": bool(counts_match),
        "purgeOk": bool(purge_ok),
        "deleteOk": bool(delete_ok),
        "purgeResult": purge_result,
        "deleteResult": delete_result,
        "restored": restored,
        "outputPath": str(output_path) if output_path else None,
    }


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="staging_governance_drill",
        description="Run staging governance drill (export -> purge -> delete -> restore -> verify).",
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--user-id", default="staging-admin")
    parser.add_argument("--roles", default="tenant_admin")
    parser.add_argument("--scopes", default="admin:write,admin:read")
    parser.add_argument("--older-than-days", type=int, default=0)
    parser.add_argument("--collections", default=",".join(SUBCOLLECTIONS))
    parser.add_argument("--data-tier", default="demo")
    parser.add_argument("--output", help="Optional snapshot output path")
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT", "ekaette"))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _create_parser()
    args = parser.parse_args(argv)

    collections = [item.strip() for item in str(args.collections).split(",")]
    output_path = Path(args.output) if args.output else None
    result = run_staging_governance_drill(
        base_url=args.base_url,
        tenant_id=args.tenant,
        company_id=args.company,
        user_id=args.user_id,
        roles_csv=args.roles,
        scopes_csv=args.scopes,
        older_than_days=int(args.older_than_days),
        collections=collections,
        data_tier=args.data_tier,
        output_path=output_path,
        project_id=str(args.project),
        dry_run=bool(args.dry_run),
        timeout_seconds=float(args.timeout),
    )
    print(json.dumps(result, indent=2))
    if not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
