"""Tests for scripts/staging_governance_drill.py."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.staging_governance_drill import run_staging_governance_drill


class _CollectionRef:
    def __init__(self, db: "_FakeDB", path: tuple[str, ...]):
        self._db = db
        self._path = path

    def document(self, doc_id: str):
        return _DocRef(self._db, self._path + (doc_id,))

    def stream(self):
        prefix_len = len(self._path)
        for path, data in list(self._db.docs.items()):
            if len(path) != prefix_len + 1:
                continue
            if path[:prefix_len] != self._path:
                continue
            yield _Doc(reference=_DocRef(self._db, path), id=path[-1], _data=data)


@dataclass
class _Doc:
    reference: "_DocRef"
    id: str
    _data: dict

    def to_dict(self):
        return dict(self._data)


class _DocRef:
    def __init__(self, db: "_FakeDB", path: tuple[str, ...]):
        self._db = db
        self._path = path

    def collection(self, name: str):
        return _CollectionRef(self._db, self._path + (name,))

    def set(self, payload: dict):
        self._db.docs[self._path] = dict(payload)

    def delete(self):
        self._db.docs.pop(self._path, None)


class _FakeDB:
    def __init__(self):
        self.docs: dict[tuple[str, ...], dict] = {}

    def collection(self, name: str):
        return _CollectionRef(self, (name,))


def _export_payload() -> dict:
    return {
        "apiVersion": "v1",
        "tenantId": "public",
        "companyId": "ekaette-hotel",
        "company": {
            "tenantId": "public",
            "companyId": "ekaette-hotel",
            "industryTemplateId": "hotel",
            "schemaVersion": 1,
        },
        "collections": {
            "knowledge": [{"id": "k-1", "title": "FAQ"}],
            "products": [{"id": "p-1", "name": "Suite", "price": 100}],
            "booking_slots": [{"id": "s-1", "date": "2026-03-01", "time": "10:00"}],
        },
        "counts": {
            "knowledge": 1,
            "products": 1,
            "booking_slots": 1,
        },
    }


def test_run_staging_governance_drill_dry_run(tmp_path: Path):
    calls: list[tuple[str, str]] = []

    def request_stub(method: str, url: str, headers: dict[str, str], payload: dict | None, timeout: float):
        calls.append((method, url))
        assert headers["X-Tenant-Id"] == "public"
        assert payload == {"includeRuntimeData": True}
        return _export_payload()

    output = tmp_path / "snapshot.json"
    result = run_staging_governance_drill(
        base_url="http://localhost:8000",
        tenant_id="public",
        company_id="ekaette-hotel",
        user_id="staging-admin",
        roles_csv="tenant_admin",
        scopes_csv="admin:write",
        older_than_days=0,
        collections=["knowledge", "products", "booking_slots"],
        data_tier="demo",
        output_path=output,
        project_id="ekaette",
        dry_run=True,
        request_json_fn=request_stub,
    )

    assert result["success"] is True
    assert result["dryRun"] is True
    assert calls == [("POST", "http://localhost:8000/api/v1/admin/companies/ekaette-hotel/export?tenantId=public")]
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["company_id"] == "ekaette-hotel"


def test_run_staging_governance_drill_roundtrip_restore(tmp_path: Path):
    calls: list[tuple[str, str]] = []
    export_calls = 0

    def request_stub(method: str, url: str, headers: dict[str, str], payload: dict | None, timeout: float):
        nonlocal export_calls
        calls.append((method, url))
        if method == "POST" and "/export?tenantId=public" in url:
            export_calls += 1
            assert payload == {"includeRuntimeData": True}
            return _export_payload()
        if method == "POST" and "/retention/purge?tenantId=public" in url:
            assert headers["Idempotency-Key"].startswith("purge-")
            assert isinstance(payload, dict)
            return {"apiVersion": "v1", "report": {"knowledge": {"deleted": 1}}}
        if method == "DELETE" and "?tenantId=public" in url:
            assert headers["Idempotency-Key"].startswith("delete-")
            return {"apiVersion": "v1", "deleted": {"company": 1}}
        raise AssertionError(f"unexpected request: {method} {url}")

    db = _FakeDB()
    output = tmp_path / "snapshot.json"
    result = run_staging_governance_drill(
        base_url="http://localhost:8000",
        tenant_id="public",
        company_id="ekaette-hotel",
        user_id="staging-admin",
        roles_csv="tenant_admin",
        scopes_csv="admin:write",
        older_than_days=0,
        collections=["knowledge", "products", "booking_slots"],
        data_tier="demo",
        output_path=output,
        project_id="ekaette",
        dry_run=False,
        request_json_fn=request_stub,
        db=db,
    )

    assert result["success"] is True
    assert result["countsMatch"] is True
    assert export_calls == 2
    assert len(calls) == 4
    assert output.exists()
    assert ("tenants", "public", "companies", "ekaette-hotel") in db.docs


def test_run_staging_governance_drill_rejects_invalid_collections():
    with pytest.raises(ValueError, match="unsupported collection"):
        run_staging_governance_drill(
            base_url="http://localhost:8000",
            tenant_id="public",
            company_id="ekaette-hotel",
            user_id="staging-admin",
            roles_csv="tenant_admin",
            scopes_csv="admin:write",
            older_than_days=0,
            collections=["knowledge", "invalid_collection"],
            data_tier="demo",
            output_path=None,
            project_id="ekaette",
            dry_run=True,
            request_json_fn=lambda *_args, **_kwargs: _export_payload(),
        )
