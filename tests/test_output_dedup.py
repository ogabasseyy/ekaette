"""Tests for output-level transcription dedup (ADK #3395 mitigation)."""

import pytest

from app.api.v1.realtime.stream_tasks import _text_overlap


class TestTextOverlap:
    """Unit tests for _text_overlap word-level similarity."""

    def test_identical_strings(self):
        assert _text_overlap("hello world", "hello world") == 1.0

    def test_completely_different(self):
        assert _text_overlap("hello world", "foo bar baz") == 0.0

    def test_near_duplicate_agent_response(self):
        a = (
            "I can definitely help with that! While I check our stock, "
            "are you looking for a specific colour?"
        )
        b = (
            "I can definitely help with that! Are you looking for a "
            "specific colour or storage size?"
        )
        overlap = _text_overlap(a, b)
        assert overlap > 0.6, f"Expected >0.6, got {overlap:.2f}"

    def test_different_topics_below_threshold(self):
        a = "Hello, ehkaitay here from Ogabassey Gadgets."
        b = "I can definitely help with that! Are you looking for a colour?"
        overlap = _text_overlap(a, b)
        assert overlap < 0.6, f"Expected <0.6, got {overlap:.2f}"

    def test_empty_string_returns_zero(self):
        assert _text_overlap("", "hello") == 0.0
        assert _text_overlap("hello", "") == 0.0
        assert _text_overlap("", "") == 0.0

    def test_case_insensitive(self):
        assert _text_overlap("Hello World", "hello world") == 1.0

    def test_subset_text(self):
        a = "I can help with that"
        b = "I can help with that and more things added here"
        overlap = _text_overlap(a, b)
        # All words of 'a' are in 'b', but 'b' has more words
        assert 0.4 < overlap < 1.0

    def test_single_word(self):
        assert _text_overlap("hello", "hello") == 1.0
        assert _text_overlap("hello", "world") == 0.0
