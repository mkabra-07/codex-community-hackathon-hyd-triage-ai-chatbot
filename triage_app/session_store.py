from copy import deepcopy
from datetime import datetime, timezone


_SESSIONS = {}


def _empty_profile():
    return {
        "symptoms": [],
        "duration": "",
        "severity": "",
        "age": "",
        "existing_conditions": "",
    }


def get_session(session_id: str) -> dict:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = {
            "id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile": _empty_profile(),
            "history": [],
        }
    return _SESSIONS[session_id]


def append_message(session_id: str, role: str, content: str, metadata=None) -> dict:
    session = get_session(session_id)
    session["history"].append(
        {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
    )
    return session


def update_profile(session_id: str, updates=None) -> dict:
    session = get_session(session_id)
    updates = updates or {}
    next_profile = deepcopy(session["profile"])

    for key, value in updates.items():
        if key == "symptoms":
            continue
        if value is not None and str(value).strip():
            next_profile[key] = value

    next_profile["symptoms"] = _dedupe(
        session["profile"].get("symptoms", []) + updates.get("symptoms", [])
    )
    session["profile"] = next_profile
    return next_profile


def _dedupe(values):
    seen = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen
