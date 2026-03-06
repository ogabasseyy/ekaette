"""Tests for booking_agent wiring and fallback purchase flow guidance."""


class TestBookingAgentTools:
    def test_booking_agent_includes_checkout_tools(self):
        from app.agents.booking_agent.agent import booking_agent

        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in booking_agent.tools
        }
        assert "check_availability" in tool_names
        assert "create_booking" in tool_names
        assert "cancel_booking" in tool_names
        assert "get_topship_delivery_quote" in tool_names
        assert "create_virtual_account_payment" in tool_names
        assert "check_payment_status" in tool_names
        assert "create_order_record" in tool_names
        assert "track_order_delivery" in tool_names

    def test_booking_instruction_mentions_purchase_fallback(self):
        from app.agents.booking_agent.agent import booking_agent

        instruction = booking_agent.instruction.lower()
        assert "delivery quote + checkout flow" in instruction
        assert "fulfillment preference" in instruction
        assert "booking is optional for completed purchases" in instruction
