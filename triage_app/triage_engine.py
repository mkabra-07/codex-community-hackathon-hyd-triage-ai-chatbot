import re
from typing import Any, Dict, List

from .session_store import append_message, update_profile


DISCLAIMER = "This is not medical advice."

RED_FLAG_PATTERNS = [
    ("chest pain", re.compile(r"\bchest pain\b", re.IGNORECASE)),
    (
        "difficulty breathing",
        re.compile(
            r"\b(shortness of breath|difficulty breathing|can't breathe|cannot breathe)\b",
            re.IGNORECASE,
        ),
    ),
    ("unconsciousness", re.compile(r"\b(unconscious|passed out|not waking up)\b", re.IGNORECASE)),
    (
        "severe bleeding",
        re.compile(r"\b(severe bleeding|bleeding heavily|won't stop bleeding)\b", re.IGNORECASE),
    ),
]

FOLLOW_UP_ORDER = [
    ("duration", "How long have these symptoms been going on?"),
    ("severity", "How severe are the symptoms right now: mild, moderate, or severe?"),
    ("age", "What age group is the patient in?"),
    (
        "existing_conditions",
        "Do you have any existing medical conditions or take regular medications?",
    ),
]


def detect_emergency_signals(message: str) -> Dict[str, Any]:
    matches = [label for label, pattern in RED_FLAG_PATTERNS if pattern.search(message)]
    return {"is_emergency": bool(matches), "matches": matches}


def merge_assessment_into_profile(session_id: str, assessment: Dict[str, Any]) -> Dict[str, Any]:
    facts = assessment.get("collected_facts", {})
    return update_profile(
        session_id,
        {
            "symptoms": assessment.get("symptoms", []),
            "duration": facts.get("duration", ""),
            "severity": facts.get("severity", ""),
            "age": facts.get("age", ""),
            "existing_conditions": facts.get("existing_conditions", ""),
        },
    )


def build_emergency_response(session_id: str, red_flags: List[str]) -> Dict[str, Any]:
    reasoning = f"Detected red-flag symptoms: {', '.join(red_flags)}."
    next_steps = [
        "Seek immediate medical attention or call emergency services now.",
        "Do not continue self-triage if symptoms feel severe or are rapidly worsening.",
    ]
    reply = " ".join(
        [
            DISCLAIMER,
            "Your symptoms may need emergency care.",
            "Please seek immediate medical attention now.",
        ]
    )

    append_message(session_id, "assistant", reply, {"risk_level": "EMERGENCY"})
    return {
        "reply": reply,
        "assessment": {
            "symptoms": red_flags,
            "risk_level": "EMERGENCY",
            "reasoning": reasoning,
            "next_steps": next_steps,
        },
        "follow_up_questions": [],
    }


def determine_missing_fields(profile: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {"key": key, "question": question}
        for key, question in FOLLOW_UP_ORDER
        if not str(profile.get(key, "")).strip()
    ]


def normalize_risk_level(assessment: Dict[str, Any], profile: Dict[str, Any]) -> str:
    severe = str(profile.get("severity", "")).lower() == "severe"
    has_conditions = bool(str(profile.get("existing_conditions", "")).strip())
    long_duration = re.search(r"\b(week|weeks|month|months)\b", str(profile.get("duration", "")), re.I)

    if assessment.get("risk_level") == "EMERGENCY":
        return "EMERGENCY"
    if severe or (long_duration and has_conditions) or assessment.get("risk_level") == "URGENT":
        return "URGENT"
    if assessment.get("risk_level") == "LOW":
        return "LOW"
    return "URGENT"


def build_assistant_reply(session_id: str, assessment: Dict[str, Any], profile: Dict[str, Any]):
    missing = determine_missing_fields(profile)
    risk_level = normalize_risk_level(assessment, profile)
    follow_up_questions = []

    if missing:
        follow_up_questions = [item["question"] for item in missing[:2]]
        reply = " ".join(
            [
                DISCLAIMER,
                "I'm assessing how urgent this may be, but I need a little more information first.",
                " ".join(follow_up_questions),
            ]
        )
    elif risk_level == "URGENT":
        reply = " ".join(
            [
                DISCLAIMER,
                "Based on the information shared, this sounds urgent enough that you should consult a doctor within 24 hours.",
                "If symptoms worsen, you develop chest pain, trouble breathing, or severe bleeding, seek emergency care immediately.",
            ]
        )
    else:
        reply = " ".join(
            [
                DISCLAIMER,
                "This does not sound like an emergency from the information shared so far.",
                "Home care may be reasonable, but contact a clinician if symptoms worsen, persist, or you feel unsure.",
            ]
        )

    final_assessment = {
        "symptoms": profile.get("symptoms", []),
        "risk_level": "URGENT" if missing else risk_level,
        "reasoning": assessment.get("reasoning", ""),
        "next_steps": _next_steps(assessment, risk_level, missing),
    }

    append_message(
        session_id,
        "assistant",
        reply,
        {
            "risk_level": final_assessment["risk_level"],
            "follow_up_questions": follow_up_questions,
        },
    )
    return {
        "reply": reply,
        "assessment": final_assessment,
        "follow_up_questions": follow_up_questions,
    }


def _next_steps(assessment: Dict[str, Any], risk_level: str, missing: List[Dict[str, str]]) -> List[str]:
    if missing:
        return [
            "Answer the follow-up questions so urgency can be assessed more reliably.",
            "If anything feels severe or rapidly worsening, contact urgent or emergency care.",
        ]

    next_steps = assessment.get("next_steps") or []
    if next_steps:
        return next_steps

    if risk_level == "LOW":
        return ["Rest, hydrate, monitor symptoms, and seek care if symptoms worsen."]
    return ["Consult a doctor within 24 hours."]
