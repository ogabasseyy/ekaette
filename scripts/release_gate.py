"""Release gate checks for registry/admin production readiness."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _read_json_or_yaml_object(path: Path) -> dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            parsed = yaml.safe_load(raw_text)
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise ValueError(f"failed to parse {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"file must contain object root: {path}")
    return parsed


def validate_policy_files(repo_root: Path) -> list[str]:
    errors: list[str] = []
    providers_path = repo_root / "policies" / "mcp_providers.v1.json"
    capability_path = repo_root / "policies" / "capability_matrix.v1.yaml"
    slos_path = repo_root / "policies" / "observability_slos.v1.yaml"
    alerts_path = repo_root / "policies" / "alert_policies.v1.json"

    if not providers_path.exists():
        errors.append(f"missing policy file: {providers_path}")
        return errors
    if not capability_path.exists():
        errors.append(f"missing policy file: {capability_path}")
        return errors
    if not slos_path.exists():
        errors.append(f"missing policy file: {slos_path}")
        return errors
    if not alerts_path.exists():
        errors.append(f"missing policy file: {alerts_path}")
        return errors

    try:
        providers_doc = _read_json_or_yaml_object(providers_path)
    except ValueError as exc:
        errors.append(str(exc))
        return errors
    providers = providers_doc.get("providers")
    if not isinstance(providers, dict) or not providers:
        errors.append("providers policy missing non-empty 'providers' map")
    else:
        required_policy_keys = {
            "timeoutSeconds",
            "maxRetries",
            "circuitOpenAfterFailures",
            "circuitOpenSeconds",
            "allowedHosts",
        }
        for provider_id, value in providers.items():
            if not isinstance(value, dict):
                errors.append(f"provider '{provider_id}' must be an object")
                continue
            test_policy = value.get("testPolicy")
            if not isinstance(test_policy, dict):
                errors.append(f"provider '{provider_id}' missing testPolicy")
                continue
            missing = sorted(required_policy_keys - set(test_policy.keys()))
            if missing:
                errors.append(f"provider '{provider_id}' testPolicy missing keys: {', '.join(missing)}")

    try:
        capability_doc = _read_json_or_yaml_object(capability_path)
    except ValueError as exc:
        errors.append(str(exc))
        return errors
    templates = capability_doc.get("templates")
    if not isinstance(templates, dict) or not templates:
        errors.append("capability matrix missing non-empty 'templates' map")

    try:
        slos_doc = _read_json_or_yaml_object(slos_path)
    except ValueError as exc:
        errors.append(str(exc))
        return errors
    slos = slos_doc.get("slos")
    if not isinstance(slos, dict) or not slos:
        errors.append("observability SLO policy missing non-empty 'slos' map")
    else:
        required_slos = {
            "onboarding_config",
            "runtime_bootstrap",
            "token",
            "websocket_startup",
            "registry_resolution",
        }
        missing = sorted(required_slos - set(slos.keys()))
        if missing:
            errors.append(f"observability SLO policy missing entries: {', '.join(missing)}")

        for slo_id in sorted(required_slos.intersection(slos.keys())):
            value = slos.get(slo_id)
            if not isinstance(value, dict):
                errors.append(f"observability SLO '{slo_id}' must be an object")
                continue
            latency_value = value.get("latencyP95Ms")
            if not isinstance(latency_value, (int, float)) or latency_value <= 0:
                errors.append(f"observability SLO '{slo_id}' has invalid latencyP95Ms")
            if slo_id == "registry_resolution":
                metric_value = value.get("metric")
                if not isinstance(metric_value, str) or not metric_value.strip():
                    errors.append("observability SLO 'registry_resolution' missing metric")
                continue
            path_value = value.get("path")
            if not isinstance(path_value, str) or not path_value.strip():
                errors.append(f"observability SLO '{slo_id}' missing path")
            error_rate = value.get("errorRatePercent")
            if not isinstance(error_rate, (int, float)) or error_rate < 0:
                errors.append(f"observability SLO '{slo_id}' has invalid errorRatePercent")
            if slo_id == "token":
                miss_rate = value.get("registryMissRatePercent")
                if not isinstance(miss_rate, (int, float)) or miss_rate < 0:
                    errors.append("observability SLO 'token' has invalid registryMissRatePercent")

    try:
        alerts_doc = _read_json_or_yaml_object(alerts_path)
    except ValueError as exc:
        errors.append(str(exc))
        return errors
    alert_entries = alerts_doc.get("alerts")
    if not isinstance(alert_entries, list) or not alert_entries:
        errors.append("alert policy missing non-empty 'alerts' list")
    else:
        for index, value in enumerate(alert_entries):
            if not isinstance(value, dict):
                errors.append(f"alert policy entry at index {index} must be an object")
                continue
            required_keys = {"id", "severity", "metric", "window", "condition", "description"}
            missing = sorted(required_keys - set(value.keys()))
            if missing:
                errors.append(f"alert policy entry {index} missing keys: {', '.join(missing)}")
                continue
            if not isinstance(value.get("id"), str) or not str(value.get("id")).strip():
                errors.append(f"alert policy entry {index} has invalid id")
            severity = value.get("severity")
            if severity not in {"info", "warning", "critical"}:
                errors.append(f"alert policy entry {index} has invalid severity")
            condition = value.get("condition")
            if not isinstance(condition, dict):
                errors.append(f"alert policy entry {index} has invalid condition")
                continue
            operator = condition.get("operator")
            if operator not in {"gt", "gte", "lt", "lte", "eq"}:
                errors.append(f"alert policy entry {index} has invalid condition.operator")
            threshold = condition.get("threshold")
            if not isinstance(threshold, (int, float)):
                errors.append(f"alert policy entry {index} has invalid condition.threshold")

    return errors


def validate_required_artifacts(repo_root: Path) -> list[str]:
    errors: list[str] = []
    required_paths = [
        repo_root / "docs" / "runbooks" / "admin-data-governance-operations.md",
        repo_root / "scripts" / "dr_restore_drill.py",
        repo_root / "docs" / "operational-readiness-slos-alerts.md",
        repo_root / "scripts" / "staging_governance_drill.py",
    ]
    for path in required_paths:
        if not path.exists():
            errors.append(f"missing required artifact: {path}")
    return errors


def run_release_gates(
    *,
    repo_root: Path,
    run_docs_check: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    policy_errors = validate_policy_files(repo_root)
    checks.append(
        {
            "name": "policy_files",
            "passed": not policy_errors,
            "errors": policy_errors,
        }
    )

    artifact_errors = validate_required_artifacts(repo_root)
    checks.append(
        {
            "name": "required_artifacts",
            "passed": not artifact_errors,
            "errors": artifact_errors,
        }
    )

    if run_docs_check:
        cmd = [sys.executable, str(repo_root / "scripts" / "check_local_docs.py")]
        result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
        checks.append(
            {
                "name": "local_docs_manifest",
                "passed": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        )

    failed = [check for check in checks if not check.get("passed")]
    return {
        "success": len(failed) == 0,
        "checks": checks,
        "failedCount": len(failed),
    }


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_gate",
        description="Run release gate checks for admin/registry production controls.",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-docs-check", action="store_true", default=False)
    parser.add_argument("--strict", action="store_true", default=False)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _create_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    result = run_release_gates(repo_root=repo_root, run_docs_check=bool(args.run_docs_check))
    print(json.dumps(result, indent=2))

    if args.strict and not result.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
