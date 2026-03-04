from app.configs.host_allowlist import extract_connector_endpoint_host, host_matches_allowlist


def test_extract_connector_endpoint_host_handles_credentials_and_port():
    connector = {
        "config": {
            "endpoint": "https://user:pass@api.salesforce.com:8443/services/data"
        }
    }
    assert extract_connector_endpoint_host(connector) == "api.salesforce.com"


def test_extract_connector_endpoint_host_handles_ipv6_literal():
    connector = {"config": {"endpoint": "https://[2001:db8::1]:443/v1"}}
    assert extract_connector_endpoint_host(connector) == "2001:db8::1"


def test_extract_connector_endpoint_host_returns_none_for_invalid_config():
    assert extract_connector_endpoint_host({"config": {"endpoint": "not-a-url"}}) is None
    assert extract_connector_endpoint_host({"config": {}}) is None
    assert extract_connector_endpoint_host({}) is None


def test_extract_connector_endpoint_host_supports_non_http_url_scheme():
    connector = {"config": {"endpoint": "ftp://files.example.com:21/home"}}
    assert extract_connector_endpoint_host(connector) == "files.example.com"


def test_host_matches_allowlist_exact_and_wildcard():
    assert host_matches_allowlist("api.salesforce.com", ["api.salesforce.com"]) is True
    assert host_matches_allowlist("foo.salesforce.com", ["*.salesforce.com"]) is True
    assert host_matches_allowlist("salesforce.com", ["*.salesforce.com"]) is False


def test_host_matches_allowlist_rejects_ambiguous_wildcard_matches():
    assert host_matches_allowlist("evil-salesforce.com", ["*.salesforce.com"]) is False
    assert host_matches_allowlist("foo.salesforce.com.evil.com", ["*.salesforce.com"]) is False


def test_host_matches_allowlist_handles_tld_wildcard_and_empty_list():
    assert host_matches_allowlist("example.com", ["*.com"]) is True
    assert host_matches_allowlist("com", ["*.com"]) is False
    assert host_matches_allowlist("example.com", []) is False
