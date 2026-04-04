from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .database import (
    fetch_recent_conversations,
    get_cached_history_summary,
    get_latest_conversation_id,
    upsert_history_summary,
)


MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_CHARS = 500


def build_context(user: Dict[str, Any], history_summary: str, current_input: str) -> Dict[str, Any]:
    return {
        "profile": {
            "age": user.get("age") if user.get("age") not in (None, "") else "unknown",
            "conditions": _format_conditions(user.get("existing_conditions")),
            "gender": user.get("gender") or "unknown",
        },
        "history_summary": (history_summary or "No relevant prior history available.").strip(),
        "current_message": (current_input or "").strip(),
    }


def build_history_summary(
    db_path: str,
    user_id: str,
    session_history: List[Dict[str, Any]] | None = None,
) -> str:
    cached = get_cached_history_summary(db_path, user_id)
    latest_message_id = get_latest_conversation_id(db_path, user_id)

    if cached and cached["last_message_id"] == latest_message_id:
        cached_text = str(cached["summary"].get("text", "")).strip()
        if cached_text:
            return cached_text

    recent_messages = fetch_recent_conversations(db_path, user_id, limit=MAX_HISTORY_MESSAGES)
    combined = _merge_messages(recent_messages, session_history or [])
    summary_text = _summarize_messages(combined)

    upsert_history_summary(db_path, user_id, latest_message_id, {"text": summary_text})
    return summary_text


def build_current_symptom_snapshot(
    triage_state: Dict[str, Any],
    latest_user_message: str,
) -> str:
    details: List[str] = []
    symptoms = triage_state.get("symptoms") or []
    if symptoms:
        details.append(f"Symptoms: {', '.join(symptoms)}")
    if triage_state.get("duration"):
        details.append(f"Duration: {triage_state['duration']}")
    if triage_state.get("severity"):
        details.append(f"Severity: {triage_state['severity']}")

    for key, value in (triage_state.get("additional_answers") or {}).items():
        if value:
            label = key.replace("_", " ")
            details.append(f"{label}: {value}")

    if latest_user_message.strip():
        details.append(f"Latest patient message: {latest_user_message.strip()}")

    return "; ".join(details)[:MAX_HISTORY_CHARS] or latest_user_message.strip()


def _merge_messages(
    stored_messages: List[Dict[str, Any]],
    session_history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    existing_keys = {
        (item.get("role"), item.get("message"), item.get("created_at"))
        for item in stored_messages
    }
    merged = list(stored_messages)

    for item in session_history:
        key = (item.get("role"), item.get("content"), item.get("timestamp"))
        if key in existing_keys:
            continue
        merged.append(
            {
                "role": item.get("role"),
                "message": item.get("content", ""),
                "created_at": item.get("timestamp"),
            }
        )

    return merged[-MAX_HISTORY_MESSAGES:]


def _summarize_messages(messages: Iterable[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for item in messages:
        role = "Patient" if item.get("role") == "user" else "Assistant"
        message = " ".join(str(item.get("message", "")).split())
        if not message:
            continue
        lines.append(f"{role}: {message}")

    summary = " | ".join(lines)
    if not summary:
        return "No relevant prior history available."
    if len(summary) <= MAX_HISTORY_CHARS:
        return summary
    return summary[: MAX_HISTORY_CHARS - 3].rstrip() + "..."


def _format_conditions(value: Any) -> str:
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(cleaned) if cleaned else "none reported"
    text = str(value or "").strip()
    return text or "none reported"
