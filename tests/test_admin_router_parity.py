"""Route parity checks for admin router extraction."""

from __future__ import annotations


def test_admin_route_parity_after_router_extraction():
    from main import app

    expected = {
        ("POST", "/api/v1/admin/companies"),
        ("GET", "/api/v1/admin/companies"),
        ("GET", "/api/v1/admin/companies/{company_id}"),
        ("PUT", "/api/v1/admin/companies/{company_id}"),
        ("GET", "/api/v1/admin/mcp/providers"),
        ("GET", "/api/v1/admin/companies/{company_id}/knowledge"),
        ("POST", "/api/v1/admin/companies/{company_id}/knowledge/import-text"),
        ("POST", "/api/v1/admin/companies/{company_id}/knowledge/import-url"),
        ("POST", "/api/v1/admin/companies/{company_id}/knowledge/import-file"),
        ("DELETE", "/api/v1/admin/companies/{company_id}/knowledge/{knowledge_id}"),
        ("POST", "/api/v1/admin/companies/{company_id}/connectors"),
        ("PUT", "/api/v1/admin/companies/{company_id}/connectors/{connector_id}"),
        ("POST", "/api/v1/admin/companies/{company_id}/connectors/{connector_id}/test"),
        ("DELETE", "/api/v1/admin/companies/{company_id}/connectors/{connector_id}"),
        ("POST", "/api/v1/admin/companies/{company_id}/products/import"),
        ("POST", "/api/v1/admin/companies/{company_id}/booking-slots/import"),
        ("POST", "/api/v1/admin/companies/{company_id}/runtime/purge-demo"),
        ("POST", "/api/v1/admin/companies/{company_id}/export"),
        ("DELETE", "/api/v1/admin/companies/{company_id}"),
        ("POST", "/api/v1/admin/companies/{company_id}/retention/purge"),
    }

    found = set()
    endpoint_modules = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not isinstance(path, str) or not path.startswith("/api/v1/admin"):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            found.add((method, path))
            endpoint_modules.append(getattr(route.endpoint, "__module__", ""))

    assert found == expected
    assert len(found) == 20
    assert endpoint_modules
    assert all(module.startswith("app.api.v1.admin.routes") for module in endpoint_modules)
