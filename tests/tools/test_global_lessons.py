"""Tests for the global lessons system — load, classify, inject, submit."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.tools.global_lessons import (
    LESSON_CATEGORIES,
    classify_lesson_scope,
    format_lessons_for_instruction,
    load_global_lessons,
    submit_global_lesson,
    validate_global_lesson,
)


# ═══ Schema Validation ═══


class TestValidateGlobalLesson:
    """Validate lesson document structure."""

    def test_valid_lesson_passes(self):
        lesson = {
            "id": "skip-power-question-if-visible",
            "lesson": "If vision analysis shows the device is powered on, do not ask if it powers on.",
            "category": "vision_behavior",
            "applicable_agents": ["valuation_agent"],
            "status": "active",
            "source": "customer_feedback",
            "trigger_count": 1,
        }
        assert validate_global_lesson(lesson) == []

    def test_missing_id_fails(self):
        lesson = {
            "lesson": "Some lesson",
            "category": "vision_behavior",
            "status": "active",
        }
        errors = validate_global_lesson(lesson)
        assert any("id" in e for e in errors)

    def test_missing_lesson_text_fails(self):
        lesson = {
            "id": "test",
            "category": "vision_behavior",
            "status": "active",
        }
        errors = validate_global_lesson(lesson)
        assert any("lesson" in e for e in errors)

    def test_invalid_category_fails(self):
        lesson = {
            "id": "test",
            "lesson": "Some lesson",
            "category": "invalid_category",
            "status": "active",
        }
        errors = validate_global_lesson(lesson)
        assert any("category" in e for e in errors)

    def test_invalid_status_fails(self):
        lesson = {
            "id": "test",
            "lesson": "Some lesson",
            "category": "vision_behavior",
            "status": "banana",
        }
        errors = validate_global_lesson(lesson)
        assert any("status" in e for e in errors)

    def test_not_a_dict_fails(self):
        assert validate_global_lesson("string") == ["global_lesson must be a dict"]

    def test_applicable_agents_must_be_list(self):
        lesson = {
            "id": "test",
            "lesson": "Some lesson",
            "category": "vision_behavior",
            "status": "active",
            "applicable_agents": "valuation_agent",
        }
        errors = validate_global_lesson(lesson)
        assert any("applicable_agents" in e for e in errors)

    def test_optional_fields_default_ok(self):
        """Minimal valid lesson — only required fields."""
        lesson = {
            "id": "test",
            "lesson": "Some lesson",
            "category": "vision_behavior",
            "status": "active",
        }
        assert validate_global_lesson(lesson) == []

    def test_all_categories_are_valid(self):
        for cat in LESSON_CATEGORIES:
            lesson = {
                "id": "test",
                "lesson": "Some lesson",
                "category": cat,
                "status": "active",
            }
            assert validate_global_lesson(lesson) == [], f"Category {cat} should be valid"


# ═══ Load Lessons from Firestore ═══


class TestLoadGlobalLessons:
    """Load active lessons from Firestore, scoped to tenant/company."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        return db

    def _setup_mock_docs(self, mock_db, docs: list[dict[str, Any]]):
        """Wire mock Firestore to return given documents."""
        mock_docs = []
        for doc in docs:
            mock_doc = MagicMock()
            mock_doc.to_dict.return_value = doc
            mock_doc.id = doc.get("id", "unknown")
            mock_docs.append(mock_doc)

        mock_stream = MagicMock()
        mock_stream.stream.return_value = mock_docs

        mock_lessons_col = MagicMock()
        mock_lessons_col.where.return_value = mock_stream

        mock_company_doc = MagicMock()
        mock_company_doc.collection.return_value = mock_lessons_col

        mock_companies_col = MagicMock()
        mock_companies_col.document.return_value = mock_company_doc

        mock_tenant_doc = MagicMock()
        mock_tenant_doc.collection.return_value = mock_companies_col

        mock_tenants_col = MagicMock()
        mock_tenants_col.document.return_value = mock_tenant_doc

        mock_db.collection.return_value = mock_tenants_col

    def test_loads_active_lessons(self, mock_db):
        lessons = [
            {
                "id": "lesson-1",
                "lesson": "Don't ask redundant questions.",
                "category": "questionnaire_logic",
                "applicable_agents": ["valuation_agent"],
                "status": "active",
                "source": "admin",
                "trigger_count": 5,
            },
            {
                "id": "lesson-2",
                "lesson": "Mention video option for device assessment.",
                "category": "vision_behavior",
                "applicable_agents": ["ekaette_router"],
                "status": "active",
                "source": "customer_feedback",
                "trigger_count": 3,
            },
        ]
        self._setup_mock_docs(mock_db, lessons)

        result = load_global_lessons(mock_db, tenant_id="public", company_id="ekaette-electronics")
        assert len(result) == 2
        assert result[0]["id"] == "lesson-1"
        assert result[1]["id"] == "lesson-2"

    def test_returns_empty_when_no_lessons(self, mock_db):
        self._setup_mock_docs(mock_db, [])
        result = load_global_lessons(mock_db, tenant_id="public", company_id="ekaette-electronics")
        assert result == []

    def test_returns_empty_when_db_is_none(self):
        result = load_global_lessons(None, tenant_id="public", company_id="ekaette-electronics")
        assert result == []

    def test_filters_invalid_lessons(self, mock_db):
        """Lessons that fail validation are excluded."""
        lessons = [
            {
                "id": "good",
                "lesson": "Valid lesson.",
                "category": "vision_behavior",
                "status": "active",
            },
            {
                "id": "bad",
                "lesson": "",
                "category": "vision_behavior",
                "status": "active",
            },
        ]
        self._setup_mock_docs(mock_db, lessons)
        result = load_global_lessons(mock_db, tenant_id="public", company_id="ekaette-electronics")
        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_handles_firestore_exception_gracefully(self, mock_db):
        mock_db.collection.side_effect = Exception("Firestore down")
        result = load_global_lessons(mock_db, tenant_id="public", company_id="ekaette-electronics")
        assert result == []


# ═══ Format Lessons for Instruction Injection ═══


class TestFormatLessonsForInstruction:
    """Format loaded lessons into instruction text for the model."""

    def test_formats_single_lesson(self):
        lessons = [
            {
                "id": "skip-power-q",
                "lesson": "If the device is visibly powered on, skip the power-on question.",
                "category": "questionnaire_logic",
                "applicable_agents": ["valuation_agent"],
            },
        ]
        text = format_lessons_for_instruction(lessons, agent_name="valuation_agent")
        assert "powered on" in text
        assert "LEARNED BEHAVIORS" in text

    def test_filters_by_agent_name(self):
        lessons = [
            {
                "id": "l1",
                "lesson": "Lesson for valuation only.",
                "category": "questionnaire_logic",
                "applicable_agents": ["valuation_agent"],
            },
            {
                "id": "l2",
                "lesson": "Lesson for vision only.",
                "category": "vision_behavior",
                "applicable_agents": ["vision_agent"],
            },
        ]
        text = format_lessons_for_instruction(lessons, agent_name="valuation_agent")
        assert "valuation only" in text
        assert "vision only" not in text

    def test_wildcard_agent_matches_all(self):
        lessons = [
            {
                "id": "l1",
                "lesson": "Universal lesson.",
                "category": "general",
                "applicable_agents": ["*"],
            },
        ]
        text = format_lessons_for_instruction(lessons, agent_name="booking_agent")
        assert "Universal lesson" in text

    def test_no_applicable_agents_matches_all(self):
        """Lessons without applicable_agents field apply to all agents."""
        lessons = [
            {
                "id": "l1",
                "lesson": "Applies to everyone.",
                "category": "general",
            },
        ]
        text = format_lessons_for_instruction(lessons, agent_name="support_agent")
        assert "Applies to everyone" in text

    def test_returns_empty_string_for_no_lessons(self):
        assert format_lessons_for_instruction([], agent_name="ekaette_router") == ""

    def test_returns_empty_string_when_no_lessons_match_agent(self):
        lessons = [
            {
                "id": "l1",
                "lesson": "Only for vision.",
                "category": "vision_behavior",
                "applicable_agents": ["vision_agent"],
            },
        ]
        text = format_lessons_for_instruction(lessons, agent_name="booking_agent")
        assert text == ""


# ═══ Classify Lesson Scope ═══


class TestClassifyLessonScope:
    """Determine if a correction is user-specific or global."""

    def test_personal_info_is_user_scoped(self):
        scope = classify_lesson_scope("My name is Chidi and I prefer morning pickups.")
        assert scope == "user"

    def test_behavioral_correction_is_global(self):
        scope = classify_lesson_scope(
            "You shouldn't ask if the device powers on when you can see it's turned on in the video."
        )
        assert scope == "global"

    def test_preference_is_user_scoped(self):
        scope = classify_lesson_scope("I live in Victoria Island now, not Lekki.")
        assert scope == "user"

    def test_process_improvement_is_global(self):
        scope = classify_lesson_scope(
            "Always suggest video instead of just photos for better assessment."
        )
        assert scope == "global"

    def test_ambiguous_defaults_to_user(self):
        scope = classify_lesson_scope("Ok thanks.")
        assert scope == "user"


# ═══ Submit Global Lesson ═══


class TestSubmitGlobalLesson:
    """Write a new global lesson to Firestore."""

    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.set = MagicMock()

        mock_lessons_col = MagicMock()
        mock_lessons_col.document.return_value = mock_doc_ref

        mock_company_doc = MagicMock()
        mock_company_doc.collection.return_value = mock_lessons_col

        mock_companies_col = MagicMock()
        mock_companies_col.document.return_value = mock_company_doc

        mock_tenant_doc = MagicMock()
        mock_tenant_doc.collection.return_value = mock_companies_col

        mock_tenants_col = MagicMock()
        mock_tenants_col.document.return_value = mock_tenant_doc

        db.collection.return_value = mock_tenants_col
        return db

    def test_submits_lesson_as_pending(self, mock_db):
        result = submit_global_lesson(
            mock_db,
            tenant_id="public",
            company_id="ekaette-electronics",
            lesson_text="Don't ask redundant questions the camera answered.",
            category="questionnaire_logic",
            applicable_agents=["valuation_agent"],
            source="customer_feedback",
        )
        assert result["status"] == "pending_review"
        assert result["lesson"] == "Don't ask redundant questions the camera answered."
        assert "id" in result

    def test_auto_promotes_when_admin_source(self, mock_db):
        result = submit_global_lesson(
            mock_db,
            tenant_id="public",
            company_id="ekaette-electronics",
            lesson_text="Important admin lesson.",
            category="general",
            source="admin",
        )
        assert result["status"] == "active"

    def test_returns_none_when_db_missing(self):
        result = submit_global_lesson(
            None,
            tenant_id="public",
            company_id="ekaette-electronics",
            lesson_text="Test",
            category="general",
        )
        assert result is None


# ═══ Callback Integration ═══


class TestLessonInjectionInCallback:
    """Test that lessons are injected via before_model_inject_config."""

    def test_lessons_appended_to_instruction(self):
        lessons = [
            {
                "id": "l1",
                "lesson": "Skip power question if device is visibly on.",
                "category": "questionnaire_logic",
                "applicable_agents": ["*"],
            },
        ]
        text = format_lessons_for_instruction(lessons, agent_name="ekaette_router")
        assert "Skip power question" in text
        assert "LEARNED BEHAVIORS" in text

    def test_empty_lessons_produce_no_injection(self):
        text = format_lessons_for_instruction([], agent_name="ekaette_router")
        assert text == ""
