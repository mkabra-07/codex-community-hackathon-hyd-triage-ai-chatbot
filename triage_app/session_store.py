from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict


_SESSIONS = {}


def _empty_profile() -> Dict[str, object]:
    return {
        "symptoms": [],
        "duration": "",
        "severity": "",
        "age": "",
        "gender": "",
        "height": "",
        "weight": "",
        "existing_conditions": "",
    }


def get_session(session_key: str, base_profile=None) -> dict:
    if session_key not in _SESSIONS:
        _SESSIONS[session_key] = {
            "id": session_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile": _empty_profile(),
            "history": [],
        }

    session = _SESSIONS[session_key]
    if base_profile:
        session["profile"] = {
            **session["profile"],
            **{key: value for key, value in base_profile.items() if value not in (None, "")},
        }
    return session


def append_message(session_key: str, role: str, content: str, metadata=None) -> dict:
    session = get_session(session_key)
    session["history"].append(
        {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        }
    )
    return session


def update_profile(session_key: str, updates=None) -> dict:
    session = get_session(session_key)
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
