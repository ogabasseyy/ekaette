from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _MockSnapshot:
    def __init__(self, data: dict | None):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _MockContext:
    def __init__(self, state: dict | None = None):
        self.state = state or {}


@pytest.mark.asyncio
async def test_request_media_via_whatsapp_creates_context_doc_and_sends_message():
    from app.tools.cross_channel_tools import request_media_via_whatsapp

    ctx = _MockContext(
        state={
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
            "app:session_id": "voice-session-1",
            "app:user_id": "phone-user-1",
            "user:caller_phone": "+2348012345678",
        }
    )

    mock_doc = SimpleNamespace(id="ctx-doc-1")
    with patch("app.tools.cross_channel_tools._get_firestore_db", return_value=MagicMock()), \
         patch("app.tools.cross_channel_tools._context_doc_ref", return_value=mock_doc), \
         patch("app.tools.cross_channel_tools._doc_set", new_callable=AsyncMock) as mock_set, \
         patch("app.tools.cross_channel_tools._doc_update", new_callable=AsyncMock) as mock_update, \
         patch(
             "app.tools.cross_channel_tools.send_whatsapp_message",
             new_callable=AsyncMock,
             return_value={"status": "sent", "message_id": "wamid-1"},
         ) as mock_send:
        result = await request_media_via_whatsapp(
            reason="trade_in_photo_requested",
            summary="Customer wants to trade in an iPhone XR for an iPhone 14 128GB.",
            tool_context=ctx,
        )

    assert result["status"] == "sent"
    assert result["context_id"] == "ctx-doc-1"
    payload = mock_set.await_args.args[1]
    assert payload["tenant_id"] == "public"
    assert payload["company_id"] == "ekaette-electronics"
    assert payload["phone"] == "+2348012345678"
    assert payload["pending_reason"] == "trade_in_photo_requested"
    assert "iPhone XR" in payload["conversation_summary"]
    assert ctx.state["temp:cross_channel_media_request_pending"] is True
    sent_text = mock_send.await_args.args[0]
    assert "send a clear photo or short video" in sent_text.lower()
    assert "do not need to repeat yourself" in sent_text.lower()
    assert "\n" not in sent_text
    assert mock_send.await_args.kwargs["template_name"] == "tradein_media_request"
    assert mock_send.await_args.kwargs["template_language"] == "en_US"
    mock_update.assert_awaited()


@pytest.mark.asyncio
async def test_request_media_via_whatsapp_returns_error_when_store_unavailable():
    from app.tools.cross_channel_tools import request_media_via_whatsapp

    ctx = _MockContext(
        state={
            "app:tenant_id": "public",
            "app:company_id": "ekaette-electronics",
            "user:caller_phone": "+2348012345678",
        }
    )

    with patch("app.tools.cross_channel_tools._get_firestore_db", return_value=None):
        result = await request_media_via_whatsapp(
            reason="trade_in_photo_requested",
            summary="Customer wants to trade in an iPhone XR.",
            tool_context=ctx,
        )

    assert result["status"] == "error"
    assert "context store" in result["detail"].lower()


@pytest.mark.asyncio
async def test_request_media_via_whatsapp_requires_tenant_and_company_context():
    from app.tools.cross_channel_tools import request_media_via_whatsapp

    ctx = _MockContext(
        state={
            "user:caller_phone": "+2348012345678",
        }
    )

    result = await request_media_via_whatsapp(
        reason="trade_in_photo_requested",
        summary="Customer wants to trade in an iPhone XR.",
        tool_context=ctx,
    )

    assert result["status"] == "error"
    assert "missing tenant or company context" in result["detail"].lower()


@pytest.mark.asyncio
async def test_load_and_consume_cross_channel_context_returns_pending_doc_and_marks_consumed():
    from app.tools.cross_channel_tools import load_and_consume_cross_channel_context

    mock_doc = MagicMock()
    mock_doc.get.return_value = _MockSnapshot(
        {
            "status": "pending",
            "created_at": 4102444800.0 - 60.0,
            "conversation_summary": "Customer wants to trade in an iPhone XR.",
            "pending_reason": "trade_in_photo_requested",
        }
    )

    with patch("time.time", return_value=4102444800.0), \
         patch("app.tools.cross_channel_tools._get_firestore_db", return_value=object()), \
         patch("app.tools.cross_channel_tools._context_doc_ref", return_value=mock_doc):
        result = await load_and_consume_cross_channel_context(
            tenant_id="public",
            company_id="ekaette-electronics",
            phone="+2348012345678",
        )

    assert result is not None
    assert result["status"] == "consumed"
    assert result["conversation_summary"] == "Customer wants to trade in an iPhone XR."
    mock_doc.update.assert_called_once()
    update_payload = mock_doc.update.call_args.args[0]
    assert update_payload["status"] == "consumed"


@pytest.mark.asyncio
async def test_load_and_consume_cross_channel_context_returns_none_for_expired_doc():
    from app.tools.cross_channel_tools import load_and_consume_cross_channel_context

    mock_doc = MagicMock()
    mock_doc.get.return_value = _MockSnapshot(
        {
            "status": "pending",
            "created_at": 4102444800.0 - 3600.0,
            "conversation_summary": "Expired context",
            "pending_reason": "trade_in_photo_requested",
        }
    )

    with patch("time.time", return_value=4102444800.0), \
         patch("app.tools.cross_channel_tools._get_firestore_db", return_value=object()), \
         patch("app.tools.cross_channel_tools._context_doc_ref", return_value=mock_doc):
        result = await load_and_consume_cross_channel_context(
            tenant_id="public",
            company_id="ekaette-electronics",
            phone="+2348012345678",
        )

    assert result is None
    mock_doc.update.assert_called_once()
    update_payload = mock_doc.update.call_args.args[0]
    assert update_payload["status"] == "expired"


def test_extract_snapshot_data_requires_existing_doc():
    from app.tools.cross_channel_tools import _extract_snapshot_data

    snapshot = SimpleNamespace(exists=False, to_dict=lambda: {"status": "pending"})
    assert _extract_snapshot_data(snapshot) is None


def test_validate_pending_context_requires_explicit_pending_status():
    from app.tools.cross_channel_tools import _validate_pending_context

    with patch("time.time", return_value=4_102_444_800.0):
        data, terminal_status = _validate_pending_context(
            {
                "status": "",
                "created_at": 4_102_444_800.0 - 60.0,
                "conversation_summary": "Customer wants to trade in an iPhone XR.",
            }
        )

    assert data is None
    assert terminal_status is None
