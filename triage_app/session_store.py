from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
from typing import Dict


class Stage(StrEnum):
    SYMPTOM_COLLECTION = "SYMPTOM_COLLECTION"
    DURATION_COLLECTION = "DURATION_COLLECTION"
    SEVERITY_COLLECTION = "SEVERITY_COLLECTION"
    FOLLOW_UPS = "FOLLOW_UPS"
    TRIAGE_RESULT = "TRIAGE_RESULT"


_SESSIONS = {}


def _empty_profile() -> Dict[str, object]:
    return {
        "age": "",
        "gender": "",
        "height": "",
        "weight": "",
        "existing_conditions": "",
    }


def _empty_triage_state() -> Dict[str, object]:
    return {
        "stage": Stage.SYMPTOM_COLLECTION,
        "symptoms": [],
        "duration": None,
        "duration_days": None,
        "duration_value_hours": None,
        "severity": None,
        "additional_answers": {},
        "pending_follow_ups": [],
        "completed_follow_ups": [],
        "history_summary": [],
        "last_result": None,
        "pending_confirmation": None,
    }


def get_session(session_key: str, base_profile=None) -> dict:
    if session_key not in _SESSIONS:
        _SESSIONS[session_key] = {
            "id": session_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile": _empty_profile(),
            "history": [],
            "triage": _empty_triage_state(),
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
        if value is not None and str(value).strip():
            next_profile[key] = value

    session["profile"] = next_profile
    return next_profile


def get_triage_state(session_key: str) -> dict:
    return get_session(session_key)["triage"]


def update_triage_state(session_key: str, updates=None) -> dict:
    session = get_session(session_key)
    triage = deepcopy(session["triage"])
    updates = updates or {}

    for key, value in updates.items():
        triage[key] = value

    session["triage"] = triage
    return triage


def reset_triage_state(session_key: str) -> dict:
    session = get_session(session_key)
    session["triage"] = _empty_triage_state()
    return session["triage"]
