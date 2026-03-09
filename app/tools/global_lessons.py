"""Global Lessons — Cross-session behavioral learning for all users.

Two-tier memory architecture:
- Tier 1 (existing): Per-user memories via ADK Memory Bank
- Tier 2 (this module): Global lessons stored in Firestore, loaded at session
  init, and injected into agent instructions via before_model_callback.

Firestore path: tenants/{tenant_id}/companies/{company_id}/global_lessons/{id}
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ═══ Constants ═══

LESSON_CATEGORIES = frozenset({
    "vision_behavior",
    "questionnaire_logic",
    "greeting",
    "general",
    "routing",
    "tone",
})

_VALID_STATUSES = frozenset({"active", "pending_review", "retired"})

# Keywords that indicate personal/user-specific content (not global lessons).
_USER_SCOPE_PATTERNS = re.compile(
    r"\b(my name|i live|i moved|i prefer|i am|i\'m|my address|my phone|my email"
    r"|my number|call me|i work|my location|my account)\b",
    re.IGNORECASE,
)

# Keywords that indicate behavioral/process corrections (global lessons).
_GLOBAL_SCOPE_PATTERNS = re.compile(
    r"\b(you should|you shouldn\'t|don\'t ask|always |never |stop asking"
    r"|next time|in the future|for everyone|remember to|instead of"
    r"|better to|suggest|improve|every time)\b",
    re.IGNORECASE,
)


# ═══ Schema Validation ═══


def validate_global_lesson(data: Any) -> list[str]:
    """Validate a global lesson document. Returns list of error strings (empty = valid)."""
    if not isinstance(data, dict):
        return ["global_lesson must be a dict"]

    errors: list[str] = []

    for field in ("id", "lesson"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty required string field: {field}")

    category = data.get("category")
    if not isinstance(category, str) or category not in LESSON_CATEGORIES:
        errors.append(
            f"invalid category: {category!r} (must be one of {sorted(LESSON_CATEGORIES)})"
        )

    status = data.get("status")
    if not isinstance(status, str) or status not in _VALID_STATUSES:
        errors.append(
            f"invalid status: {status!r} (must be one of {sorted(_VALID_STATUSES)})"
        )

    applicable_agents = data.get("applicable_agents")
    if applicable_agents is not None:
        if not isinstance(applicable_agents, list):
            errors.append("applicable_agents must be a list if present")
        elif not all(isinstance(a, str) for a in applicable_agents):
            errors.append("applicable_agents items must be strings")

    return errors


# ═══ Load from Firestore ═══


def _global_lessons_query(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
) -> Any:
    return (
        db.collection("tenants")
        .document(tenant_id)
        .collection("companies")
        .document(company_id)
        .collection("global_lessons")
        .where("status", "==", "active")
        .limit(50)
    )


def _validated_lessons_from_docs(docs: list[Any]) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict()
        if not isinstance(data, dict):
            continue
        validation_errors = validate_global_lesson(data)
        if validation_errors:
            logger.debug("Skipping invalid global lesson: %s", doc.id)
            continue
        lessons.append(data)
    return lessons


async def _collect_query_docs(query: Any) -> list[Any]:
    """Collect Firestore query stream results from sync or async clients."""
    stream_result = query.stream()
    if inspect.isawaitable(stream_result):
        stream_result = await stream_result
    if hasattr(stream_result, "__aiter__"):
        return [doc async for doc in stream_result]
    return await asyncio.to_thread(lambda: list(stream_result))


def load_global_lessons(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
) -> list[dict[str, Any]]:
    """Load active global lessons from Firestore.

    Path: tenants/{tenant_id}/companies/{company_id}/global_lessons
    Filters: status == "active"
    Returns empty list on any error (non-blocking).
    """
    if db is None:
        return []

    try:
        docs = _global_lessons_query(
            db, tenant_id=tenant_id, company_id=company_id,
        ).stream()
        if inspect.isawaitable(docs) or hasattr(docs, "__aiter__"):
            logger.warning(
                "Failed to load global lessons: async Firestore client requires "
                "aload_global_lessons()"
            )
            return []
        return _validated_lessons_from_docs(list(docs))
    except Exception as exc:
        logger.warning("Failed to load global lessons: %s", exc)
        return []


async def aload_global_lessons(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
) -> list[dict[str, Any]]:
    """Load active global lessons from sync or async Firestore clients."""
    if db is None:
        return []

    try:
        docs = await _collect_query_docs(
            _global_lessons_query(db, tenant_id=tenant_id, company_id=company_id)
        )
        return _validated_lessons_from_docs(docs)
    except Exception as exc:
        logger.warning("Failed to load global lessons: %s", exc)
        return []


# ═══ Format for Instruction Injection ═══


def _lesson_applies_to_agent(
    lesson: dict[str, Any],
    agent_name: str,
) -> bool:
    """Check if a lesson applies to the given agent."""
    applicable = lesson.get("applicable_agents")
    if not isinstance(applicable, list) or not applicable:
        return True  # No restriction = applies to all
    return "*" in applicable or agent_name in applicable


def format_lessons_for_instruction(
    lessons: list[dict[str, Any]],
    *,
    agent_name: str,
) -> str:
    """Format global lessons into instruction text for injection.

    Filters by agent_name. Returns empty string if no lessons match.
    """
    matching = [
        lesson for lesson in lessons
        if _lesson_applies_to_agent(lesson, agent_name)
    ]

    if not matching:
        return ""

    lines = ["LEARNED BEHAVIORS (apply these in every conversation):"]
    for lesson in matching:
        text = lesson.get("lesson", "").strip()
        if text:
            lines.append(f"- {text}")

    return "\n".join(lines)


# ═══ Classify Lesson Scope ═══


def classify_lesson_scope(text: str) -> str:
    """Classify whether a customer statement is user-specific or a global lesson.

    Uses keyword heuristics. Returns "user" or "global".
    Defaults to "user" when ambiguous (safer — avoids polluting global lessons).
    """
    has_global = bool(_GLOBAL_SCOPE_PATTERNS.search(text))
    has_user = bool(_USER_SCOPE_PATTERNS.search(text))

    if has_global and not has_user:
        return "global"
    # User patterns win ties, and ambiguous defaults to user
    return "user"


# ═══ Submit to Firestore ═══


def submit_global_lesson(
    db: Any,
    *,
    tenant_id: str,
    company_id: str,
    lesson_text: str,
    category: str,
    applicable_agents: list[str] | None = None,
    source: str = "customer_feedback",
) -> dict[str, Any] | None:
    """Write a new global lesson to Firestore.

    Admin-sourced lessons are auto-promoted to active.
    Customer-sourced lessons start as pending_review.
    Returns the lesson dict on success, None on failure.
    """
    if db is None:
        return None

    if not lesson_text or not lesson_text.strip():
        logger.warning("Cannot submit global lesson with empty lesson_text")
        return None

    if category not in LESSON_CATEGORIES:
        logger.warning("Invalid category %r for global lesson", category)
        return None

    status = "active" if source == "admin" else "pending_review"
    lesson_id = f"lesson-{uuid.uuid4().hex[:12]}"

    lesson: dict[str, Any] = {
        "id": lesson_id,
        "lesson": lesson_text,
        "category": category,
        "status": status,
        "source": source,
        "trigger_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if applicable_agents:
        lesson["applicable_agents"] = applicable_agents

    try:
        col = (
            db.collection("tenants")
            .document(tenant_id)
            .collection("companies")
            .document(company_id)
            .collection("global_lessons")
        )
        col.document(lesson_id).set(lesson)
        return lesson
    except Exception as exc:
        logger.warning("Failed to submit global lesson: %s", exc)
        return None
