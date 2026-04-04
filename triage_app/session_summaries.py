from __future__ import annotations

from math import ceil
from typing import Any, Dict, List, Tuple

from .database import (
    count_sessions,
    fetch_session_messages,
    fetch_session_overview,
    fetch_session_record,
    upsert_session_summary,
)


SESSION_SUMMARY_TRIGGER_MESSAGES = 4
SESSION_SUMMARY_FALLBACK_LENGTH = 58

SESSION_SUMMARY_PROMPT = """
Summarize this chat session in 1 or 2 short lines.
Capture the user's main intent and keep it concise.
Do not include speaker labels, timestamps, or extra formatting.
Return plain text only.
""".strip()


def list_sessions_page(
    *,
    db_path: str,
    user_id: str,
    client,
    model: str,
    page: int = 1,
    per_page: int = 20,
) -> Dict[str, Any]:
    safe_page = max(page, 1)
    safe_per_page = min(max(per_page, 1), 50)
    offset = (safe_page - 1) * safe_per_page

    sessions = fetch_session_overview(db_path, user_id, limit=safe_per_page, offset=offset)
    items = [
        _materialize_session_summary(
            db_path=db_path,
            user_id=user_id,
            record=record,
            client=client,
            model=model,
        )
        for record in sessions
    ]
    total = count_sessions(db_path, user_id)

    return {
        "sessions": items,
        "page": safe_page,
        "per_page": safe_per_page,
        "total": total,
        "has_more": offset + len(items) < total,
        "total_pages": ceil(total / safe_per_page) if total else 0,
    }


def get_session_detail(
    *,
    db_path: str,
    user_id: str,
    session_id: str,
    client,
    model: str,
) -> Dict[str, Any] | None:
    record = fetch_session_record(db_path, user_id, session_id)
    if not record:
        return None

    session = _materialize_session_summary(
        db_path=db_path,
        user_id=user_id,
        record=record,
        client=client,
        model=model,
    )
    messages = fetch_session_messages(db_path, user_id, session_id)

    return {
        **session,
        "messages": messages,
    }


def maybe_refresh_session_summary(
    *,
    db_path: str,
    user_id: str,
    session_id: str,
    client,
    model: str,
    force: bool = False,
    trigger_message_count: int = SESSION_SUMMARY_TRIGGER_MESSAGES,
) -> str | None:
    record = fetch_session_record(db_path, user_id, session_id)
    if not record:
        return None

    is_stale = (
        not record.get("cached_summary")
        or record.get("summary_last_message_id") != record.get("last_message_id")
    )
    should_refresh = force or record["message_count"] >= trigger_message_count

    if not should_refresh or not is_stale:
        return record.get("cached_summary")

    summary, messages = _generate_summary_text(
        db_path=db_path,
        user_id=user_id,
        session_id=session_id,
        client=client,
        model=model,
    )
    _store_session_summary(db_path=db_path, user_id=user_id, record=record, summary=summary)

    return summary


def generate_session_summary(messages: List[Dict[str, Any]], client, model: str) -> str:
    fallback = _fallback_session_summary(messages)
    if client is None:
        return fallback

    transcript = "\n".join(
        f"{item.get('role', 'unknown')}: {_message_text(item)}"
        for item in messages
        if _message_text(item)
    )
    if not transcript:
        return fallback

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SESSION_SUMMARY_PROMPT},
                {"role": "user", "content": f"Session transcript:\n{transcript}"},
            ],
        )
        raw_summary = response.choices[0].message.content or ""
    except Exception:
        return fallback

    summary = _normalize_summary(raw_summary)
    return summary or fallback


def _materialize_session_summary(
    *,
    db_path: str,
    user_id: str,
    record: Dict[str, Any],
    client,
    model: str,
) -> Dict[str, Any]:
    summary = record.get("cached_summary")
    is_stale = (
        not summary
        or record.get("summary_last_message_id") != record.get("last_message_id")
    )

    if is_stale:
        summary, _messages = _generate_summary_text(
            db_path=db_path,
            user_id=user_id,
            session_id=record["session_id"],
            client=client,
            model=model,
        )
        _store_session_summary(db_path=db_path, user_id=user_id, record=record, summary=summary)

    return {
        "session_id": record["session_id"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "message_count": record["message_count"],
        "summary": summary or "New Chat",
    }


def _generate_summary_text(
    *,
    db_path: str,
    user_id: str,
    session_id: str,
    client,
    model: str,
) -> Tuple[str, List[Dict[str, Any]]]:
    messages = fetch_session_messages(db_path, user_id, session_id)
    summary = generate_session_summary(messages, client, model)
    return summary, messages


def _store_session_summary(
    *,
    db_path: str,
    user_id: str,
    record: Dict[str, Any],
    summary: str,
) -> None:
    upsert_session_summary(
        db_path,
        user_id=user_id,
        session_id=record["session_id"],
        created_at=record["created_at"],
        last_message_id=record["last_message_id"],
        message_count=record["message_count"],
        summary=summary,
    )


def _fallback_session_summary(messages: List[Dict[str, Any]]) -> str:
    if not messages:
        return "New Chat"

    first_user_message = next(
        (_message_text(item) for item in messages if item.get("role") == "user" and _message_text(item)),
        "",
    )
    if first_user_message:
        return _trim_text(first_user_message, SESSION_SUMMARY_FALLBACK_LENGTH)

    first_message = next((_message_text(item) for item in messages if _message_text(item)), "")
    if first_message:
        return _trim_text(first_message, SESSION_SUMMARY_FALLBACK_LENGTH)

    return "New Chat"


def _message_text(message: Dict[str, Any]) -> str:
    return str(message.get("message") or message.get("content") or "").strip()


def _normalize_summary(text: str) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    return _trim_text(compact, 120)


def _trim_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."
