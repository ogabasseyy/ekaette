"""Tests for scripts/dr_restore_drill.py."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

try:
    from scripts.dr_restore_drill import (
        delete_company_bundle,
        export_company_snapshot,
        restore_company_snapshot,
        run_restore_drill,
    )
except ImportError:
    pytest.skip("scripts.dr_restore_drill not yet implemented", allow_module_level=True)


class _Snapshot:
    def __init__(self, exists: bool, data: dict | None = None):
        self.exists = exists
        self._data = data or {}

    def to_dict(self):
        return dict(self._data)


@dataclass
class _Doc:
    reference: "_DocRef"
    id: str
    _data: dict

    def to_dict(self):
        return dict(self._data)


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


class _DocRef:
    def __init__(self, db: "_FakeDB", path: tuple[str, ...]):
        self._db = db
        self._path = path

    def collection(self, name: str):
        return _CollectionRef(self._db, self._path + (name,))

    def get(self):
        data = self._db.docs.get(self._path)
        return _Snapshot(exists=data is not None, data=data)

    def set(self, payload: dict):
        self._db.docs[self._path] = dict(payload)

    def delete(self):
        self._db.docs.pop(self._path, None)


class _FakeDB:
    def __init__(self):
        self.docs: dict[tuple[str, ...], dict] = {}

    def collection(self, name: str):
        return _CollectionRef(self, (name,))


def _seed_company(db: _FakeDB, *, tenant_id: str, company_id: str):
    db.docs[("tenants", tenant_id, "companies", company_id)] = {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "industry_template_id": "hotel",
        "schema_version": 1,
    }
    db.docs[("tenants", tenant_id, "companies", company_id, "knowledge", "kb-1")] = {
        "id": "kb-1",
        "title": "FAQ",
    }
    db.docs[("tenants", tenant_id, "companies", company_id, "products", "p-1")] = {
        "id": "p-1",
        "name": "Phone",
    }
    db.docs[("tenants", tenant_id, "companies", company_id, "booking_slots", "s-1")] = {
        "id": "s-1",
        "date": "2026-03-01",
    }


def test_export_company_snapshot_returns_expected_counts():
    db = _FakeDB()
    _seed_company(db, tenant_id="public", company_id="ekaette-hotel")

    result = export_company_snapshot(db, tenant_id="public", company_id="ekaette-hotel")
    assert result["tenant_id"] == "public"
    assert result["company_id"] == "ekaette-hotel"
    assert result["counts"] == {"knowledge": 1, "products": 1, "booking_slots": 1}


def test_delete_and_restore_bundle_roundtrip():
    db = _FakeDB()
    _seed_company(db, tenant_id="public", company_id="ekaette-hotel")

    snapshot = export_company_snapshot(db, tenant_id="public", company_id="ekaette-hotel")
    deleted = delete_company_bundle(db, tenant_id="public", company_id="ekaette-hotel")
    assert deleted["company"] == 1

    restored = restore_company_snapshot(db, snapshot)
    assert restored["company"] == 1

    verify = export_company_snapshot(db, tenant_id="public", company_id="ekaette-hotel")
    assert verify["counts"] == snapshot["counts"]


def test_run_restore_drill_dry_run_writes_snapshot(tmp_path: Path):
    db = _FakeDB()
    _seed_company(db, tenant_id="public", company_id="ekaette-hotel")
    output = tmp_path / "snapshot.json"

    result = run_restore_drill(
        db,
        tenant_id="public",
        company_id="ekaette-hotel",
        output_path=output,
        dry_run=True,
    )
    assert result["success"] is True
    assert result["dry_run"] is True
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["company_id"] == "ekaette-hotel"

