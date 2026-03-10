"""Tests for static handoff placeholders in specialist agent instructions."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("module_path", "agent_attr", "expected_target"),
    [
        ("app.agents.catalog_agent.agent", "catalog_agent", "catalog_agent"),
        ("app.agents.booking_agent.agent", "booking_agent", "booking_agent"),
        ("app.agents.support_agent.agent", "support_agent", "support_agent"),
        ("app.agents.valuation_agent.agent", "valuation_agent", "valuation_agent"),
        ("app.agents.vision_agent.agent", "vision_agent", "vision_agent"),
    ],
)
def test_specialist_agent_instruction_includes_pending_handoff_placeholders(
    module_path: str,
    agent_attr: str,
    expected_target: str,
) -> None:
    module = importlib.import_module(module_path)
    agent = getattr(module, agent_attr)
    instruction = str(agent.instruction)

    assert "pending_handoff_target_agent" in instruction
    assert "pending_handoff_latest_user" in instruction
    assert "pending_handoff_latest_agent" in instruction
    assert "pending_handoff_recent_customer_context" in instruction
    assert expected_target in instruction
