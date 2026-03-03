"""Tests for WhatsApp call session (wa_session.py).

Covers:
- Session creation with codec_bridge + srtp_context
- TaskGroup structured concurrency (3 tasks)
- Initial SRTP packet sending (Meta requirement)
- Shutdown signal and clean teardown
- Frame counting metrics
"""

from __future__ import annotations

import asyncio
import os

import pytest

try:
    __import__("opuslib_next")
    _has_opuslib = True
except ImportError:
    _has_opuslib = False

try:
    __import__("pylibsrtp")
    _has_pylibsrtp = True
except ImportError:
    _has_pylibsrtp = False


class TestWaSessionCreation:
    """WaSession dataclass fields and defaults."""

    def test_session_has_call_id(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
        )
        assert s.call_id == "wa-call-1"

    def test_session_has_bounded_queues(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
        )
        assert s.inbound_queue.maxsize > 0
        assert s.outbound_queue.maxsize > 0

    def test_session_initial_metrics_zero(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
        )
        assert s.frames_received == 0
        assert s.frames_sent == 0

    @pytest.mark.skipif(not _has_opuslib, reason="opuslib_next not installed")
    def test_session_accepts_codec_bridge(self):
        from sip_bridge.codec_bridge import OpusCodecBridge
        from sip_bridge.wa_session import WaSession

        bridge = OpusCodecBridge(encode_rate=16000)
        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            codec_bridge=bridge,
        )
        assert s.codec_bridge is bridge

    @pytest.mark.skipif(not _has_pylibsrtp, reason="pylibsrtp not installed")
    def test_session_accepts_srtp_contexts(self):
        from sip_bridge.srtp_context import SRTPContext
        from sip_bridge.wa_session import WaSession

        key = os.urandom(30)
        sender = SRTPContext(key_material=key, is_sender=True)
        receiver = SRTPContext(key_material=key, is_sender=False)
        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            srtp_sender=sender,
            srtp_receiver=receiver,
        )
        assert s.srtp_sender is sender
        assert s.srtp_receiver is receiver


class TestWaSessionShutdown:
    """Graceful shutdown."""

    def test_shutdown_sets_event(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(call_id="wa-call-1", tenant_id="public", company_id="acme")
        assert not s._shutdown.is_set()
        s.shutdown()
        assert s._shutdown.is_set()

    async def test_run_completes_after_shutdown(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(call_id="wa-call-1", tenant_id="public", company_id="acme")

        async def signal_shutdown():
            await asyncio.sleep(0.05)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(signal_shutdown())

        assert s._shutdown.is_set()


class TestWaSessionInbound:
    """Inbound frame feeding and backpressure."""

    async def test_feed_inbound_increments_counter(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(call_id="wa-call-1", tenant_id="public", company_id="acme")
        await s.feed_inbound(b"\x00" * 200)
        assert s.frames_received == 1

    async def test_feed_inbound_drops_when_full(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(call_id="wa-call-1", tenant_id="public", company_id="acme")
        # Fill the queue
        for _ in range(s.inbound_queue.maxsize):
            await s.feed_inbound(b"\x00" * 200)
        # This should be dropped
        await s.feed_inbound(b"\x00" * 200)
        assert s.inbound_drops == 1


class TestWaSessionOutbound:
    """Outbound queue and frame counting."""

    async def test_outbound_frame_counting(self):
        from sip_bridge.wa_session import WaSession

        s = WaSession(call_id="wa-call-1", tenant_id="public", company_id="acme")

        for _ in range(3):
            await s.outbound_queue.put(b"\x00" * 200)

        async def run_briefly():
            await asyncio.sleep(0.1)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(run_briefly())

        assert s.frames_sent == 3


class TestWaSessionMediaPipeline:
    """Media loops must use codec_bridge + srtp_context (not TODO stubs)."""

    async def test_inbound_loop_calls_srtp_unprotect(self):
        """_media_inbound_loop must call srtp_receiver.unprotect on frames."""
        from unittest.mock import AsyncMock, MagicMock

        from sip_bridge.wa_session import WaSession

        mock_srtp = MagicMock()
        mock_srtp.unprotect.return_value = b"\x80\x00" + b"\x00" * 158  # RTP header + payload
        mock_codec = MagicMock()
        mock_codec.decode_to_pcm16_16k.return_value = b"\x00" * 640

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            codec_bridge=mock_codec,
            srtp_receiver=mock_srtp,
        )
        await s.feed_inbound(b"\x00" * 200)

        async def stop_soon():
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        mock_srtp.unprotect.assert_called()

    async def test_inbound_loop_calls_codec_decode(self):
        """_media_inbound_loop must decode RTP payload via codec_bridge."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_srtp = MagicMock()
        mock_srtp.unprotect.return_value = b"\x80\x00" + b"\x00" * 158
        mock_codec = MagicMock()
        mock_codec.decode_to_pcm16_16k.return_value = b"\x00" * 640

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            codec_bridge=mock_codec,
            srtp_receiver=mock_srtp,
        )
        await s.feed_inbound(b"\x00" * 200)

        async def stop_soon():
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        mock_codec.decode_to_pcm16_16k.assert_called()

    async def test_outbound_loop_calls_codec_encode(self):
        """_media_outbound_loop must encode PCM via codec_bridge."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_srtp = MagicMock()
        mock_srtp.protect.return_value = b"\x00" * 200
        mock_codec = MagicMock()
        mock_codec.encode_from_pcm16_24k.return_value = b"\x00" * 80

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            codec_bridge=mock_codec,
            srtp_sender=mock_srtp,
        )
        # Put PCM frames into outbound queue
        for _ in range(2):
            await s.outbound_queue.put(b"\x00" * 960)

        async def stop_soon():
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        mock_codec.encode_from_pcm16_24k.assert_called()

    async def test_outbound_loop_calls_srtp_protect(self):
        """_media_outbound_loop must SRTP-protect encoded frames."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_srtp = MagicMock()
        mock_srtp.protect.return_value = b"\x00" * 200
        mock_codec = MagicMock()
        mock_codec.encode_from_pcm16_24k.return_value = b"\x00" * 80

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            codec_bridge=mock_codec,
            srtp_sender=mock_srtp,
        )
        await s.outbound_queue.put(b"\x00" * 960)

        async def stop_soon():
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        mock_srtp.protect.assert_called()


class TestWaSessionFirestorePersistence:
    """Call-state persistence to Firestore."""

    async def test_session_writes_call_start_to_firestore(self):
        """Session run() must write call start record to Firestore."""
        from unittest.mock import AsyncMock, MagicMock

        from sip_bridge.wa_session import WaSession

        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref
        mock_doc_ref.set = MagicMock()

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            firestore_db=mock_db,
        )

        async def stop_soon():
            await asyncio.sleep(0.05)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        mock_doc_ref.set.assert_called()
        # First call should be the "started" write
        first_call_args = mock_doc_ref.set.call_args_list[0]
        data = first_call_args[0][0]
        assert data["status"] == "active"
        assert data["call_id"] == "wa-call-1"

    async def test_session_writes_call_end_to_firestore(self):
        """Session run() must write call end record on shutdown."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref
        mock_doc_ref.set = MagicMock()
        mock_doc_ref.update = MagicMock()

        s = WaSession(
            call_id="wa-call-1",
            tenant_id="public",
            company_id="acme",
            firestore_db=mock_db,
        )

        async def stop_soon():
            await asyncio.sleep(0.05)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        # Should have an update call for termination
        mock_doc_ref.update.assert_called()
        end_data = mock_doc_ref.update.call_args[0][0]
        assert end_data["status"] == "terminated"

    async def test_session_uses_call_id_as_document_id(self):
        """Firestore doc ID should be the call_id for natural idempotency."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_db.collection.return_value = mock_collection
        mock_doc_ref = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_doc_ref.set = MagicMock()
        mock_doc_ref.update = MagicMock()

        s = WaSession(
            call_id="wa-call-xyz",
            tenant_id="public",
            company_id="acme",
            firestore_db=mock_db,
        )

        async def stop_soon():
            await asyncio.sleep(0.05)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        mock_collection.document.assert_called_with("wa-call-xyz")


class TestWaSessionGeminiBidi:
    """Gemini Live bidi loop integration."""

    async def test_gemini_loop_sends_decoded_audio(self):
        """Decoded PCM16 from inbound should be sent to Gemini client."""
        from unittest.mock import AsyncMock, MagicMock

        from sip_bridge.wa_session import WaSession

        mock_gemini = AsyncMock()
        mock_gemini.send_realtime_input = AsyncMock()
        mock_gemini.close = AsyncMock()

        # receive() returns an async generator (matches real genai API)
        async def empty_receive():
            return
            yield  # make it an async generator

        mock_gemini.receive = empty_receive

        s = WaSession(
            call_id="wa-call-gemini",
            tenant_id="public",
            company_id="acme",
            gemini_session=mock_gemini,
        )
        # Put decoded PCM into the internal gemini queue
        await s._gemini_in_queue.put(b"\x00" * 640)

        async def _run_with_stop():
            async def stop_soon():
                await asyncio.sleep(0.1)
                s._shutdown.set()

            async with asyncio.TaskGroup() as tg:
                tg.create_task(s.run())
                tg.create_task(stop_soon())

        await asyncio.wait_for(_run_with_stop(), timeout=5.0)
        mock_gemini.send_realtime_input.assert_called()

    async def test_gemini_loop_queues_response_audio(self):
        """Audio from Gemini should be queued for outbound encoding."""
        from unittest.mock import AsyncMock, MagicMock

        from sip_bridge.wa_session import WaSession

        # Build a mock Gemini session that yields one audio response.
        mock_gemini = AsyncMock()
        mock_gemini.send_realtime_input = AsyncMock()
        mock_gemini.close = AsyncMock()

        call_count = 0

        async def mock_receive():
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                # Only yield one response; subsequent calls idle until exit.
                while True:
                    await asyncio.sleep(1)
                    return

            response = MagicMock()
            part = MagicMock()
            part.inline_data = MagicMock()
            part.inline_data.data = b"\x00" * 960  # 24kHz PCM16 frame
            part.inline_data.mime_type = "audio/pcm"
            response.server_content = MagicMock()
            response.server_content.model_turn = MagicMock()
            response.server_content.model_turn.parts = [part]
            yield response

        mock_gemini.receive = mock_receive

        s = WaSession(
            call_id="wa-call-gemini-out",
            tenant_id="public",
            company_id="acme",
            gemini_session=mock_gemini,
        )

        async def _run_with_stop():
            async def stop_soon():
                await asyncio.sleep(0.2)
                s._shutdown.set()

            async with asyncio.TaskGroup() as tg:
                tg.create_task(s.run())
                tg.create_task(stop_soon())

        await asyncio.wait_for(_run_with_stop(), timeout=5.0)
        # Outbound queue should have received the Gemini audio
        assert s.outbound_queue.qsize() > 0 or s.frames_sent > 0


class TestWaSessionUDPTransport:
    """Outbound media transport via UDP."""

    async def test_outbound_sends_via_transport(self):
        """Protected SRTP frames should be sent via media_transport."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_transport = MagicMock()
        mock_transport.sendto = MagicMock()
        mock_srtp = MagicMock()
        mock_srtp.protect.return_value = b"\x00" * 200
        mock_codec = MagicMock()
        mock_codec.encode_from_pcm16_24k.return_value = b"\x00" * 80

        s = WaSession(
            call_id="wa-call-udp",
            tenant_id="public",
            company_id="acme",
            codec_bridge=mock_codec,
            srtp_sender=mock_srtp,
            media_transport=mock_transport,
            remote_media_addr=("157.240.19.130", 3480),
        )
        await s.outbound_queue.put(b"\x00" * 960)

        async def stop_soon():
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        mock_transport.sendto.assert_called()

    def test_session_accepts_remote_media_addr(self):
        """WaSession should store remote media address for UDP sends."""
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-call-addr",
            tenant_id="public",
            company_id="acme",
            remote_media_addr=("157.240.19.130", 3480),
        )
        assert s.remote_media_addr == ("157.240.19.130", 3480)


class TestWaSessionRunSetup:
    """run() must create gemini_session and media_transport before loops."""

    async def test_run_creates_udp_transport(self):
        """run() must create a UDP socket for media_transport when remote_media_addr is set."""
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-call-setup",
            tenant_id="public",
            company_id="acme",
            remote_media_addr=("127.0.0.1", 30000),
        )
        transport_was_created = False

        async def check_and_stop():
            nonlocal transport_was_created
            await asyncio.sleep(0.05)
            transport_was_created = s.media_transport is not None
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(check_and_stop())

        # media_transport was created during run(), cleaned up after
        assert transport_was_created

    async def test_run_creates_gemini_session_with_config(self):
        """run() must connect to Gemini Live when api_key and model_id are set."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-call-gemini-setup",
            tenant_id="public",
            company_id="acme",
            gemini_api_key="fake-key",
            gemini_model_id="gemini-test",
        )

        # Mock the genai Client so we don't make a real API call
        mock_session = AsyncMock()
        mock_session.send = AsyncMock()
        mock_session.close = AsyncMock()

        # receive() returns an async iterable (not a coroutine)
        # — matches real genai Live API behavior
        async def empty_receive():
            return
            yield  # make it an async generator

        mock_session.receive = empty_receive

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.aio.live.connect.return_value = mock_ctx

        with patch("sip_bridge.wa_session.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client

            async def stop_soon():
                await asyncio.sleep(0.05)
                s.shutdown()

            async with asyncio.TaskGroup() as tg:
                tg.create_task(s.run())
                tg.create_task(stop_soon())

        # Gemini client should have been created with the API key
        mock_genai.Client.assert_called_once()
        call_kwargs = mock_genai.Client.call_args[1]
        assert call_kwargs["api_key"] == "fake-key"
        assert "http_options" in call_kwargs

    async def test_run_cleans_up_transport_on_shutdown(self):
        """run() must close the UDP transport on shutdown."""
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-call-cleanup",
            tenant_id="public",
            company_id="acme",
            remote_media_addr=("127.0.0.1", 30000),
        )
        transport_existed = False

        async def check_and_stop():
            nonlocal transport_existed
            await asyncio.sleep(0.05)
            transport_existed = s.media_transport is not None
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(check_and_stop())

        # Transport was created during run, then cleaned up
        assert transport_existed
        assert s.media_transport is None


class TestWaSessionUDPRecvLoop:
    """Finding 1: run() must have a UDP receive loop calling feed_inbound()."""

    async def test_udp_recv_feeds_inbound_queue(self):
        """UDP packets received on bound socket must reach inbound_queue."""
        import socket as _socket

        from sip_bridge.wa_session import WaSession

        # Create a real UDP socket pair for loopback test
        recv_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.setblocking(False)
        local_port = recv_sock.getsockname()[1]

        # Bind sender to known port so we can set correct remote_media_addr
        send_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        send_sock.bind(("127.0.0.1", 0))
        send_sock.setblocking(False)
        sender_port = send_sock.getsockname()[1]

        s = WaSession(
            call_id="wa-recv-test",
            tenant_id="public",
            company_id="acme",
            media_transport=recv_sock,
            remote_media_addr=("127.0.0.1", sender_port),
        )

        async def send_and_stop():
            await asyncio.sleep(0.05)
            # Send a fake SRTP packet to the session's bound port
            send_sock.sendto(b"\x80\x00" + b"\x00" * 158, ("127.0.0.1", local_port))
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(send_and_stop())

        send_sock.close()
        # The inbound queue should have received the packet
        assert s.frames_received >= 1

    async def test_no_recv_loop_without_transport(self):
        """If no media_transport, session should still run without error."""
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-no-recv",
            tenant_id="public",
            company_id="acme",
        )

        async def stop_soon():
            await asyncio.sleep(0.05)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        assert s.frames_received == 0


class TestWaSessionTransportOwnership:
    """Finding 3: run() must not close externally-injected transports."""

    async def test_injected_transport_not_closed(self):
        """Transport injected before run() must NOT be closed by run()."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_transport = MagicMock()
        mock_transport.fileno.return_value = 99
        mock_transport.recvfrom.side_effect = BlockingIOError

        s = WaSession(
            call_id="wa-inject-test",
            tenant_id="public",
            company_id="acme",
            media_transport=mock_transport,
            remote_media_addr=("127.0.0.1", 30000),
        )

        async def stop_soon():
            await asyncio.sleep(0.05)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_soon())

        # Injected transport must NOT have been closed
        mock_transport.close.assert_not_called()
        # But it should still be accessible
        assert s.media_transport is mock_transport

    async def test_self_created_transport_is_closed(self):
        """Transport created by run() SHOULD be closed on shutdown."""
        from sip_bridge.wa_session import WaSession

        s = WaSession(
            call_id="wa-self-transport",
            tenant_id="public",
            company_id="acme",
            remote_media_addr=("127.0.0.1", 30000),
        )
        transport_existed = False

        async def check_and_stop():
            nonlocal transport_existed
            await asyncio.sleep(0.05)
            transport_existed = s.media_transport is not None
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(check_and_stop())

        assert transport_existed
        assert s.media_transport is None


class TestWaSessionErrorRecovery:
    """M4: Session must survive transient media errors without crashing.

    Key behavior: a single corrupted SRTP packet or codec error must NOT
    terminate the entire session. The loop must catch, log, and continue
    processing subsequent frames.
    """

    async def test_srtp_unprotect_error_continues_processing(self):
        """Inbound loop must survive unprotect errors and process next frames."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_srtp = MagicMock()
        # First call fails, second succeeds
        mock_srtp.unprotect.side_effect = [
            Exception("SRTP auth tag mismatch"),
            b"\x80\x00" + b"\x00" * 158,
            Exception("Another SRTP error"),
        ]

        s = WaSession(
            call_id="wa-err-srtp",
            tenant_id="public",
            company_id="acme",
            srtp_receiver=mock_srtp,
        )
        for _ in range(3):
            await s.feed_inbound(b"\x00" * 200)

        async def stop_later():
            await asyncio.sleep(0.2)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_later())

        # All 3 frames must have been attempted (loop didn't crash on first)
        assert mock_srtp.unprotect.call_count == 3

    async def test_codec_decode_error_continues_processing(self):
        """Inbound loop must survive decode errors and process next frames."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_codec = MagicMock()
        mock_codec.decode_to_pcm16_16k.side_effect = [
            Exception("Opus decode error"),
            b"\x00" * 640,
        ]

        s = WaSession(
            call_id="wa-err-codec",
            tenant_id="public",
            company_id="acme",
            codec_bridge=mock_codec,
        )
        for _ in range(2):
            await s.feed_inbound(b"\x80\x00" + b"\x00" * 158)

        async def stop_later():
            await asyncio.sleep(0.2)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_later())

        assert mock_codec.decode_to_pcm16_16k.call_count == 2

    async def test_outbound_encode_error_continues_processing(self):
        """Outbound loop must survive encode errors and process next frames."""
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import WaSession

        mock_codec = MagicMock()
        mock_codec.encode_from_pcm16_24k.side_effect = [
            Exception("Opus encode error"),
            b"\x00" * 80,
        ]

        s = WaSession(
            call_id="wa-err-encode",
            tenant_id="public",
            company_id="acme",
            codec_bridge=mock_codec,
        )
        for _ in range(2):
            await s.outbound_queue.put(b"\x00" * 960)

        async def stop_later():
            await asyncio.sleep(0.2)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(stop_later())

        assert mock_codec.encode_from_pcm16_24k.call_count == 2


class TestWaSessionSourceAddrValidation:
    """Inbound UDP must only accept packets from negotiated remote IP+port."""

    async def test_packets_from_wrong_ip_are_dropped(self):
        """UDP packets from a different IP than remote_media_addr must be dropped."""
        import socket as _socket

        from sip_bridge.wa_session import WaSession

        recv_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.setblocking(False)
        local_port = recv_sock.getsockname()[1]

        send_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        send_sock.setblocking(False)

        # Session expects media from 10.0.0.1:9999 — wrong IP
        s = WaSession(
            call_id="wa-addr-check",
            tenant_id="public",
            company_id="acme",
            media_transport=recv_sock,
            remote_media_addr=("10.0.0.1", 9999),
        )

        async def send_and_stop():
            await asyncio.sleep(0.05)
            send_sock.sendto(b"\x80\x00" + b"\x00" * 158, ("127.0.0.1", local_port))
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(send_and_stop())

        send_sock.close()
        assert s.frames_received == 0

    async def test_packets_from_wrong_port_are_dropped(self):
        """UDP packets from correct IP but wrong source port must be dropped."""
        import socket as _socket

        from sip_bridge.wa_session import WaSession

        recv_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.setblocking(False)
        local_port = recv_sock.getsockname()[1]

        # Bind sender to a known port so we can set a *different* expected port
        send_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        send_sock.bind(("127.0.0.1", 0))
        send_sock.setblocking(False)
        sender_port = send_sock.getsockname()[1]

        # Session expects media from 127.0.0.1 but a different port
        wrong_port = sender_port + 1 if sender_port < 65535 else sender_port - 1
        s = WaSession(
            call_id="wa-port-check",
            tenant_id="public",
            company_id="acme",
            media_transport=recv_sock,
            remote_media_addr=("127.0.0.1", wrong_port),
        )

        async def send_and_stop():
            await asyncio.sleep(0.05)
            # Send from sender_port — does NOT match wrong_port
            send_sock.sendto(b"\x80\x00" + b"\x00" * 158, ("127.0.0.1", local_port))
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(send_and_stop())

        send_sock.close()
        # Packet from wrong source port must NOT reach inbound queue
        assert s.frames_received == 0

    async def test_packets_from_correct_ip_and_port_are_accepted(self):
        """UDP packets matching remote_media_addr IP+port must be accepted."""
        import socket as _socket

        from sip_bridge.wa_session import WaSession

        recv_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        recv_sock.setblocking(False)
        local_port = recv_sock.getsockname()[1]

        # Bind sender to a known port so we can set the correct expected port
        send_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        send_sock.bind(("127.0.0.1", 0))
        send_sock.setblocking(False)
        sender_port = send_sock.getsockname()[1]

        # Session expects media from the sender's actual IP+port
        s = WaSession(
            call_id="wa-addr-ok",
            tenant_id="public",
            company_id="acme",
            media_transport=recv_sock,
            remote_media_addr=("127.0.0.1", sender_port),
        )

        async def send_and_stop():
            await asyncio.sleep(0.05)
            send_sock.sendto(b"\x80\x00" + b"\x00" * 158, ("127.0.0.1", local_port))
            await asyncio.sleep(0.15)
            s.shutdown()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(s.run())
            tg.create_task(send_and_stop())

        send_sock.close()
        assert s.frames_received >= 1


class TestWaSessionRTPTimestamp:
    """RTP timestamp handling for Opus (48kHz clock rate)."""

    def test_timestamp_increment_opus(self):
        """Opus at 48kHz with 20ms frames = 960 increment per frame."""
        from sip_bridge.wa_session import compute_rtp_timestamp_increment

        # Opus: 48000 Hz, 20ms frames
        increment = compute_rtp_timestamp_increment(
            clock_rate=48000, frame_duration_ms=20
        )
        assert increment == 960

    def test_timestamp_increment_g711(self):
        """G.711 at 8kHz with 20ms frames = 160 increment per frame."""
        from sip_bridge.wa_session import compute_rtp_timestamp_increment

        increment = compute_rtp_timestamp_increment(
            clock_rate=8000, frame_duration_ms=20
        )
        assert increment == 160


class TestWaCallStateTTLCleanup:
    """M4: Stale terminated call records must be purged from Firestore."""

    def test_cleanup_deletes_old_terminated_calls(self):
        """cleanup_stale_calls must delete terminated docs older than TTL."""
        import time
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import cleanup_stale_calls

        now = time.time()
        # Two docs: one old (exceeded TTL), one recent
        old_doc = MagicMock()
        old_doc.to_dict.return_value = {
            "status": "terminated",
            "ended_at": now - 7200,  # 2 hours ago
        }
        recent_doc = MagicMock()
        recent_doc.to_dict.return_value = {
            "status": "terminated",
            "ended_at": now - 60,  # 1 minute ago
        }

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.collection.return_value.where.return_value = mock_query
        mock_query.stream.return_value = [old_doc, recent_doc]

        deleted = cleanup_stale_calls(mock_db, ttl_seconds=3600)
        # Only the old doc should be deleted
        old_doc.reference.delete.assert_called_once()
        recent_doc.reference.delete.assert_not_called()
        assert deleted == 1

    def test_cleanup_skips_active_calls(self):
        """cleanup_stale_calls must not delete active call records."""
        import time
        from unittest.mock import MagicMock

        from sip_bridge.wa_session import cleanup_stale_calls

        active_doc = MagicMock()
        active_doc.to_dict.return_value = {
            "status": "active",
            "started_at": time.time() - 7200,
        }

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.collection.return_value.where.return_value = mock_query
        mock_query.stream.return_value = [active_doc]

        deleted = cleanup_stale_calls(mock_db, ttl_seconds=3600)
        active_doc.reference.delete.assert_not_called()
        assert deleted == 0

    def test_cleanup_returns_zero_on_no_db(self):
        """cleanup_stale_calls must return 0 if db is None."""
        from sip_bridge.wa_session import cleanup_stale_calls

        assert cleanup_stale_calls(None, ttl_seconds=3600) == 0
