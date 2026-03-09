from __future__ import annotations

import ipaddress
import logging


def test_resolve_advertised_ip_handles_none_public_ip(monkeypatch):
    from sip_bridge.wa_server_helpers import resolve_advertised_ip

    monkeypatch.delenv("WA_SIP_PUBLIC_IP", raising=False)

    advertised_ip = resolve_advertised_ip(
        "0.0.0.0",
        public_ip=None,
        logger=logging.getLogger("test"),
    )

    assert isinstance(advertised_ip, str)
    assert ipaddress.ip_address(advertised_ip)


def test_resolve_advertised_ip_prefers_configured_public_ip(monkeypatch):
    from sip_bridge.wa_server_helpers import resolve_advertised_ip

    monkeypatch.setenv("WA_SIP_PUBLIC_IP", "34.69.236.219")

    advertised_ip = resolve_advertised_ip(
        "0.0.0.0",
        public_ip=None,
        logger=logging.getLogger("test"),
    )

    assert advertised_ip == "34.69.236.219"


def test_resolve_advertised_ip_prefers_explicit_public_ip_over_env(monkeypatch):
    from sip_bridge.wa_server_helpers import resolve_advertised_ip

    monkeypatch.setenv("WA_SIP_PUBLIC_IP", "34.69.236.219")

    advertised_ip = resolve_advertised_ip(
        "0.0.0.0",
        public_ip="35.1.2.3",
        logger=logging.getLogger("test"),
    )

    assert advertised_ip == "35.1.2.3"
