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
        assert "end_call" in tool_names
        assert "send_sms_message" in tool_names
        assert "send_whatsapp_message" in tool_names

    def test_booking_instruction_mentions_purchase_fallback(self):
        from app.agents.booking_agent.agent import booking_agent

        instruction = booking_agent.instruction.lower()
        assert "delivery quote + checkout flow" in instruction
        assert "fulfillment preference" in instruction
        assert "booking is optional for completed purchases" in instruction

    def test_text_booking_agent_omits_outbound_message_tools(self):
        from app.agents.booking_agent.agent import create_booking_agent

        agent = create_booking_agent("gemini-3-flash-preview", channel="text")
        tool_names = {
            getattr(tool, "name", getattr(tool, "__name__", str(tool)))
            for tool in agent.tools
        }
        assert "end_call" not in tool_names
        assert "send_sms_message" not in tool_names
        assert "send_whatsapp_message" not in tool_names

    def test_text_booking_instruction_avoids_out_of_band_followup(self):
        from app.agents.booking_agent.agent import create_booking_agent

        agent = create_booking_agent("gemini-3-flash-preview", channel="text")
        instruction = agent.instruction.lower()
        assert "separate whatsapp follow-up" in instruction
        assert "do not promise" in instruction

    def test_voice_booking_instruction_mentions_sms_or_whatsapp_followup(self):
        from app.agents.booking_agent.agent import booking_agent

        instruction = booking_agent.instruction.lower()
        assert "sms or whatsapp" in instruction
        assert "send_sms_message" in instruction

    def test_booking_instruction_mentions_single_optional_accessory_upsell(self):
        from app.agents.booking_agent.agent import booking_agent

        instruction = booking_agent.instruction.lower()
        assert "offer one brief relevant accessory upsell" in instruction
        assert "before delivery fee or payment" in instruction
        assert "only offer it once per purchase flow" in instruction
        assert "screen protector or case" in instruction
