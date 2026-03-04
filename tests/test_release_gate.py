"""Tests for scripts/release_gate.py."""

from __future__ import annotations

from pathlib import Path

from scripts.release_gate import run_release_gates, validate_policy_files


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_validate_policy_files_detects_missing_test_policy(tmp_path: Path):
    _write(
        tmp_path / "policies" / "mcp_providers.v1.json",
        '{"policyVersion":"v1","providers":{"mock":{"label":"Mock"}}}',
    )
    _write(
        tmp_path / "policies" / "capability_matrix.v1.yaml",
        '{"policyVersion":"v1","templates":{"hotel":{"allowed_provider_ids":["mock"],"max_capabilities":["read"]}}}',
    )
    _write(
        tmp_path / "policies" / "observability_slos.v1.yaml",
        '{"policyVersion":"v1","slos":{"onboarding_config":{"path":"/api/onboarding/config","latencyP95Ms":300,"errorRatePercent":1},"runtime_bootstrap":{"path":"/api/v1/runtime/bootstrap","latencyP95Ms":300,"errorRatePercent":1},"token":{"path":"/api/token","latencyP95Ms":500,"errorRatePercent":1,"registryMissRatePercent":0.1},"websocket_startup":{"path":"/ws/{user_id}/{session_id}","latencyP95Ms":1500,"errorRatePercent":1},"registry_resolution":{"metric":"registry_resolution_ms","latencyP95Ms":100}}}',
    )
    _write(
        tmp_path / "policies" / "alert_policies.v1.json",
        '{"policyVersion":"v1","alerts":[{"id":"registry_miss_spike","severity":"warning","metric":"registry_miss_total","window":"5m","condition":{"operator":"gt","threshold":1},"description":"test"}]}',
    )

    errors = validate_policy_files(tmp_path)
    assert any("missing testPolicy" in error for error in errors)


def test_run_release_gates_passes_with_required_files(tmp_path: Path):
    _write(
        tmp_path / "policies" / "mcp_providers.v1.json",
        """
        {
          "policyVersion": "v1",
          "providers": {
            "mock": {
              "label": "Mock",
              "testPolicy": {
                "timeoutSeconds": 1.0,
                "maxRetries": 0,
                "circuitOpenAfterFailures": 2,
                "circuitOpenSeconds": 10,
                "allowedHosts": []
              }
            }
          }
        }
        """,
    )
    _write(
        tmp_path / "policies" / "capability_matrix.v1.yaml",
        """
        {
          "policyVersion": "v1",
          "templates": {
            "hotel": {
              "allowed_provider_ids": ["mock"],
              "max_capabilities": ["read"]
            }
          }
        }
        """,
    )
    _write(
        tmp_path / "policies" / "observability_slos.v1.yaml",
        """
        {
          "policyVersion": "v1",
          "slos": {
            "onboarding_config": {"path": "/api/onboarding/config", "latencyP95Ms": 300, "errorRatePercent": 1.0},
            "runtime_bootstrap": {"path": "/api/v1/runtime/bootstrap", "latencyP95Ms": 300, "errorRatePercent": 1.0},
            "token": {"path": "/api/token", "latencyP95Ms": 500, "errorRatePercent": 1.0, "registryMissRatePercent": 0.1},
            "websocket_startup": {"path": "/ws/{user_id}/{session_id}", "latencyP95Ms": 1500, "errorRatePercent": 1.0},
            "registry_resolution": {"metric": "registry_resolution_ms", "latencyP95Ms": 100}
          }
        }
        """,
    )
    _write(
        tmp_path / "policies" / "alert_policies.v1.json",
        """
        {
          "policyVersion": "v1",
          "alerts": [
            {
              "id": "registry_miss_spike",
              "severity": "warning",
              "metric": "registry_miss_total",
              "window": "5m",
              "condition": {"operator": "gt", "threshold": 25},
              "description": "Registry misses exceeded expected baseline."
            }
          ]
        }
        """,
    )
    _write(tmp_path / "tests" / "test_admin_v1_contracts.py", "pass\n")
    _write(tmp_path / "docs" / "runbooks" / "admin-data-governance-operations.md", "# runbook\n")
    _write(tmp_path / "docs" / "operational-readiness-slos-alerts.md", "# slos\n")
    _write(tmp_path / "scripts" / "dr_restore_drill.py", "pass\n")
    _write(tmp_path / "scripts" / "staging_governance_drill.py", "pass\n")

    result = run_release_gates(repo_root=tmp_path, run_docs_check=False)
    assert result["success"] is True
    assert result["failedCount"] == 0


def test_validate_policy_files_detects_missing_slo_entries(tmp_path: Path):
    _write(
        tmp_path / "policies" / "mcp_providers.v1.json",
        '{"policyVersion":"v1","providers":{"mock":{"label":"Mock","testPolicy":{"timeoutSeconds":1,"maxRetries":0,"circuitOpenAfterFailures":2,"circuitOpenSeconds":10,"allowedHosts":[]}}}}',
    )
    _write(
        tmp_path / "policies" / "capability_matrix.v1.yaml",
        '{"policyVersion":"v1","templates":{"hotel":{"allowed_provider_ids":["mock"],"max_capabilities":["read"]}}}',
    )
    _write(
        tmp_path / "policies" / "observability_slos.v1.yaml",
        '{"policyVersion":"v1","slos":{"runtime_bootstrap":{"path":"/api/v1/runtime/bootstrap","latencyP95Ms":300,"errorRatePercent":1}}}',
    )
    _write(
        tmp_path / "policies" / "alert_policies.v1.json",
        '{"policyVersion":"v1","alerts":[{"id":"ok","severity":"warning","metric":"m","window":"5m","condition":{"operator":"gt","threshold":1},"description":"ok"}]}',
    )

    errors = validate_policy_files(tmp_path)
    assert any("observability SLO policy missing entries" in error for error in errors)
