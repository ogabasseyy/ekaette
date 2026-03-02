#!/usr/bin/env python3
"""Minimal websocket smoke harness for Ekaette live backend.

Capabilities:
- connection stability (session_started + no rapid disconnect)
- client_ping/client_pong heartbeat
- text turns (transcripts, transfers, agent_status idle)
- silence nudge observation + optional increasing-interval assertion

This is an internal diagnostic script for local/dev testing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import websockets


NUDGE_PATTERNS = [
    re.compile(r"\bstill there\b", re.IGNORECASE),
    re.compile(r"\btake your time\b", re.IGNORECASE),
    re.compile(r"\bright here whenever you'?re ready\b", re.IGNORECASE),
    re.compile(r"\bwould you like me to explain\b", re.IGNORECASE),
]


def monotonic_ts() -> float:
    return time.monotonic()


def short(text: str, n: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."


@dataclass
class TranscriptEvent:
    at: float
    role: str
    text: str
    partial: bool


@dataclass
class SmokeState:
    session_id: str | None = None
    session_started_count: int = 0
    client_pong_count: int = 0
    transfers: list[tuple[str, str, float]] = field(default_factory=list)
    agent_status: list[tuple[str, str, float]] = field(default_factory=list)
    transcripts: list[TranscriptEvent] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    bytes_rx: int = 0
    messages_rx: int = 0

    def record_json(self, msg: dict[str, Any], at: float) -> None:
        msg_type = str(msg.get("type", ""))
        self.messages_rx += 1
        if msg_type == "session_started":
            self.session_started_count += 1
            sid = msg.get("sessionId")
            if isinstance(sid, str):
                self.session_id = sid
        elif msg_type == "client_pong":
            self.client_pong_count += 1
        elif msg_type == "agent_transfer":
            src = str(msg.get("from", ""))
            dst = str(msg.get("to", ""))
            self.transfers.append((src, dst, at))
        elif msg_type == "agent_status":
            agent = str(msg.get("agent", ""))
            status = str(msg.get("status", ""))
            self.agent_status.append((agent, status, at))
        elif msg_type == "transcription":
            role = str(msg.get("role", ""))
            text = str(msg.get("text", ""))
            partial = bool(msg.get("partial", False))
            self.transcripts.append(TranscriptEvent(at=at, role=role, text=text, partial=partial))
        elif msg_type == "error":
            self.errors.append(msg)


class LiveSmokeClient:
    def __init__(self, ws_url: str, quiet: bool = False) -> None:
        self.ws_url = ws_url
        self.quiet = quiet
        self.state = SmokeState()
        self.ws: websockets.WebSocketClientProtocol | None = None
        self._start = monotonic_ts()

    def log(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    def rel(self, at: float | None = None) -> float:
        base = self._start
        return (at or monotonic_ts()) - base

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.ws_url, max_size=None)
        self.log(f"[connect] {self.ws_url}")

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass

    async def send_json(self, payload: dict[str, Any]) -> None:
        assert self.ws is not None
        await self.ws.send(json.dumps(payload))

    async def recv_event(self, timeout: float | None = None) -> tuple[str, Any, float]:
        """Return ('json'|'bytes'|'closed', payload, timestamp)."""
        assert self.ws is not None
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except TimeoutError:
            raise
        except websockets.ConnectionClosed as exc:
            return ("closed", exc, monotonic_ts())

        at = monotonic_ts()
        if isinstance(raw, (bytes, bytearray)):
            self.state.bytes_rx += len(raw)
            return ("bytes", raw, at)

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return ("json", {"type": "invalid_json", "raw": raw}, at)

        if isinstance(msg, dict):
            self.state.record_json(msg, at)
        return ("json", msg, at)

    async def wait_for_session_started(self, timeout: float = 10.0) -> dict[str, Any]:
        deadline = monotonic_ts() + timeout
        while monotonic_ts() < deadline:
            kind, payload, at = await self.recv_event(timeout=max(0.1, deadline - monotonic_ts()))
            if kind == "closed":
                raise RuntimeError(f"Connection closed before session_started: {payload}")
            if kind == "json" and isinstance(payload, dict):
                if payload.get("type") == "session_started":
                    self.log(
                        f"[session_started] sid={payload.get('sessionId')} "
                        f"industry={payload.get('industry')} company={payload.get('companyId')}"
                    )
                    return payload
                if payload.get("type") == "error":
                    self.log(f"[error@connect] {payload}")
        raise TimeoutError("Timed out waiting for session_started")

    async def ping_pong(self, timeout: float = 5.0) -> float:
        sent_ms = int(time.time() * 1000)
        seq = int(sent_ms % 1_000_000)
        t0 = monotonic_ts()
        await self.send_json({"type": "client_ping", "seq": seq, "clientTs": sent_ms})
        deadline = monotonic_ts() + timeout
        while monotonic_ts() < deadline:
            kind, payload, _ = await self.recv_event(timeout=max(0.1, deadline - monotonic_ts()))
            if kind == "closed":
                raise RuntimeError(f"Connection closed waiting for client_pong: {payload}")
            if kind == "json" and isinstance(payload, dict) and payload.get("type") == "client_pong":
                if payload.get("seq") == seq:
                    rtt_ms = (monotonic_ts() - t0) * 1000.0
                    self.log(f"[pong] rtt={rtt_ms:.1f}ms")
                    return rtt_ms
        raise TimeoutError("Timed out waiting for client_pong")

    async def stability_window(self, seconds: float) -> None:
        if seconds <= 0:
            return
        end = monotonic_ts() + seconds
        self.log(f"[stability] observing {seconds:.1f}s")
        while monotonic_ts() < end:
            remaining = end - monotonic_ts()
            try:
                kind, payload, at = await self.recv_event(timeout=min(0.5, max(0.05, remaining)))
            except asyncio.TimeoutError:
                continue
            if kind == "closed":
                raise RuntimeError(f"Disconnected during stability window: {payload}")
            if kind == "json" and isinstance(payload, dict) and payload.get("type") == "error":
                self.log(f"[error] +{self.rel(at):.2f}s {payload}")

    async def run_turn(self, prompt: str, timeout: float = 45.0) -> dict[str, Any]:
        assert self.ws is not None
        start_at = monotonic_ts()
        start_idx = len(self.state.transcripts)
        start_transfer_idx = len(self.state.transfers)
        start_status_idx = len(self.state.agent_status)

        self.log(f"[prompt] {prompt}")
        await self.send_json({"type": "text", "text": prompt})

        saw_agent_text = False
        deadline = start_at + timeout
        while monotonic_ts() < deadline:
            try:
                kind, payload, at = await self.recv_event(timeout=max(0.1, deadline - monotonic_ts()))
            except asyncio.TimeoutError:
                continue
            if kind == "closed":
                raise RuntimeError(f"Disconnected during turn: {payload}")
            if kind != "json" or not isinstance(payload, dict):
                continue

            msg_type = payload.get("type")
            if msg_type == "transcription":
                role = str(payload.get("role", ""))
                text = str(payload.get("text", ""))
                partial = bool(payload.get("partial", False))
                if role == "agent" and text:
                    saw_agent_text = True
                if not self.quiet and not partial and text:
                    self.log(f"[tx] {role}:F {short(text)}")
            elif msg_type == "agent_transfer":
                self.log(
                    f"[transfer] {payload.get('from')} -> {payload.get('to')}"
                )
            elif msg_type == "agent_status":
                agent = str(payload.get("agent", ""))
                status = str(payload.get("status", ""))
                if status == "idle" and (saw_agent_text or agent):
                    break
            elif msg_type == "error":
                self.log(f"[error] {payload}")

        turn_transcripts = self.state.transcripts[start_idx:]
        turn_transfers = self.state.transfers[start_transfer_idx:]
        turn_status = self.state.agent_status[start_status_idx:]
        agent_finals = [
            t.text for t in turn_transcripts if t.role == "agent" and not t.partial and t.text
        ]
        user_finals = [
            t.text for t in turn_transcripts if t.role == "user" and not t.partial and t.text
        ]
        result = {
            "agent_finals": agent_finals,
            "user_finals": user_finals,
            "transfers": turn_transfers,
            "statuses": turn_status,
        }
        return result

    async def observe_silence_nudges(
        self,
        observe_seconds: float,
        min_nudges: int = 0,
        require_increasing_intervals: bool = False,
    ) -> list[tuple[float, str]]:
        """Observe assistant nudges during silence.

        Without a dedicated server debug event, this infers nudges from agent final
        transcriptions that match common silence check-in phrases.
        """
        end = monotonic_ts() + observe_seconds
        found: list[tuple[float, str]] = []
        self.log(f"[silence] observing {observe_seconds:.1f}s")
        while monotonic_ts() < end:
            try:
                kind, payload, at = await self.recv_event(timeout=min(0.5, end - monotonic_ts()))
            except asyncio.TimeoutError:
                continue
            if kind == "closed":
                raise RuntimeError(f"Disconnected during silence observation: {payload}")
            if kind != "json" or not isinstance(payload, dict):
                continue
            if payload.get("type") != "transcription":
                continue
            if payload.get("role") != "agent" or payload.get("partial"):
                continue
            text = str(payload.get("text", ""))
            if not text:
                continue
            if any(p.search(text) for p in NUDGE_PATTERNS):
                found.append((at, text))
                self.log(f"[nudge] +{self.rel(at):.2f}s {short(text)}")

        if len(found) < min_nudges:
            raise AssertionError(
                f"Expected at least {min_nudges} silence nudges, found {len(found)}"
            )

        if require_increasing_intervals and len(found) >= 3:
            intervals = [found[i][0] - found[i - 1][0] for i in range(1, len(found))]
            # Allow minor jitter; check non-decreasing with 0.75s tolerance.
            for i in range(1, len(intervals)):
                if intervals[i] + 0.75 < intervals[i - 1]:
                    raise AssertionError(
                        f"Silence nudge intervals are not increasing: {intervals}"
                    )
            self.log("[silence] intervals look non-decreasing")

        return found


def build_ws_url(base_http_url: str, user_id: str, session_id: str, industry: str, company_id: str) -> str:
    base_http_url = base_http_url.rstrip("/")
    if base_http_url.startswith("https://"):
        ws_base = "wss://" + base_http_url[len("https://") :]
    elif base_http_url.startswith("http://"):
        ws_base = "ws://" + base_http_url[len("http://") :]
    elif base_http_url.startswith("ws://") or base_http_url.startswith("wss://"):
        ws_base = base_http_url
    else:
        ws_base = "ws://" + base_http_url
    query = urlencode({"industry": industry, "company_id": company_id})
    return f"{ws_base}/ws/{user_id}/{session_id}?{query}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ekaette live websocket smoke harness")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user-id", default="smoke-user")
    parser.add_argument("--session-id", default=f"smoke-{int(time.time())}")
    parser.add_argument("--industry", default="hotel")
    parser.add_argument("--company-id", default="ekaette-hotel")
    parser.add_argument("--stability-seconds", type=float, default=0.0)
    parser.add_argument("--prompt", action="append", default=[], help="Repeat for multiple turns")
    parser.add_argument("--turn-timeout", type=float, default=45.0)
    parser.add_argument("--silence-observe-seconds", type=float, default=0.0)
    parser.add_argument("--silence-min-nudges", type=int, default=0)
    parser.add_argument("--require-increasing-silence-intervals", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    ws_url = build_ws_url(
        base_http_url=args.base_url,
        user_id=args.user_id,
        session_id=args.session_id,
        industry=args.industry,
        company_id=args.company_id,
    )
    client = LiveSmokeClient(ws_url=ws_url, quiet=args.quiet)

    try:
        await client.connect()
        await client.wait_for_session_started()
        await client.ping_pong()
        await client.stability_window(args.stability_seconds)

        for prompt in args.prompt:
            result = await client.run_turn(prompt, timeout=args.turn_timeout)
            if not args.quiet:
                if result["transfers"]:
                    last = result["transfers"][-1]
                    print(f"[turn-summary] transfers={len(result['transfers'])} last={last[0]}->{last[1]}")
                if result["agent_finals"]:
                    print(f"[turn-summary] agent_final={short(result['agent_finals'][-1], 180)}")
                else:
                    print("[turn-summary] no agent final transcription captured")

        if args.silence_observe_seconds > 0:
            await client.observe_silence_nudges(
                observe_seconds=args.silence_observe_seconds,
                min_nudges=args.silence_min_nudges,
                require_increasing_intervals=args.require_increasing_silence_intervals,
            )

        print(
            json.dumps(
                {
                    "ok": True,
                    "session_started": client.state.session_started_count,
                    "client_pong": client.state.client_pong_count,
                    "session_id": client.state.session_id,
                    "transfers": len(client.state.transfers),
                    "errors": [e.get("code") for e in client.state.errors],
                    "bytes_rx": client.state.bytes_rx,
                    "messages_rx": client.state.messages_rx,
                }
            )
        )
        return 0
    except (AssertionError, TimeoutError, asyncio.TimeoutError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    except asyncio.CancelledError:
        print(json.dumps({"ok": False, "error": "cancelled"}), file=sys.stderr)
        return 1
    finally:
        await client.close()


def main() -> int:
    args = parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
