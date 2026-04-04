import re
from typing import Any, Dict, List


BODY_PARTS = [
    "head",
    "chest",
    "stomach",
    "back",
    "knee",
    "leg",
    "arm",
    "shoulder",
    "elbow",
]

CONDITIONS = ["pain", "ache", "swelling", "tightness", "burning"]

LIMB_PARTS = {"knee", "leg", "arm", "shoulder", "elbow"}

SYMPTOM_MAP = {
    "headache": ["headache", "head pain", "head hurts", "migraine"],
    "joint pain": ["knee pain", "elbow pain", "shoulder pain", "arm pain", "leg pain", "joint pain"],
    "abdominal pain": ["stomach pain", "stomach burning", "abdominal pain", "belly pain"],
    "chest pain": ["chest pain", "chest tightness"],
    "breathing difficulty": [
        "difficulty breathing",
        "breathing difficulty",
        "shortness of breath",
        "can't breathe",
        "cannot breathe",
    ],
    "fever": ["fever", "high temperature", "feeling hot", "temperature"],
    "cough": ["cough", "dry cough", "wet cough", "coughing"],
    "fatigue": ["fatigue", "tired", "exhausted", "weakness"],
    "vomiting": ["vomiting", "vomit", "threw up", "throwing up"],
    "cold": ["cold", "runny nose", "nasal congestion", "stuffy nose"],
    "back pain": ["back pain", "back ache"],
}

_DURATION_PATTERN = re.compile(
    r"^\s*(\d+)\s*(hour|hours|day|days|week|weeks)\s*$",
    re.IGNORECASE,
)


def validate_and_normalize_symptom(text: str, llm_fallback=None) -> Dict[str, Any]:
    symptoms = extract_and_normalize_symptoms(text)

    if not symptoms and llm_fallback:
        fallback = llm_fallback(text)
        if fallback.get("valid") and fallback.get("normalized"):
            symptoms = dedupe_list(
                [item.strip().lower() for item in fallback["normalized"] if item.strip()]
            )

    if not symptoms:
        return {
            "valid": False,
            "classification": "UNKNOWN",
            "error": (
                "I couldn't recognize that as a medical symptom. Can you describe what "
                "you're feeling physically (e.g., pain, fever, headache)?"
            ),
            "symptoms": [],
            "recognized_symptom": None,
        }

    return {
        "valid": True,
        "classification": "VALID_SYMPTOM",
        "symptoms": symptoms,
        "recognized_symptom": ", ".join(symptoms),
    }


def extract_and_normalize_symptoms(text: str) -> List[str]:
    normalized = (text or "").lower().strip()
    found: List[str] = []

    found.extend(_body_part_condition_matches(normalized))
    found.extend(_dictionary_matches(normalized))

    return dedupe_list(found)


def validate_symptoms(text: str, llm_fallback=None) -> Dict[str, Any]:
    return validate_and_normalize_symptom(text, llm_fallback=llm_fallback)


def validate_duration(text: str) -> Dict[str, object]:
    match = _DURATION_PATTERN.match(text or "")
    if not match:
        return {
            "valid": False,
            "error": "Please enter a clear duration like 2 days, 3 hours, or 1 week.",
            "duration": None,
            "duration_value_hours": None,
        }

    amount = int(match.group(1))
    unit = match.group(2).lower()
    multiplier = {
        "hour": 1,
        "hours": 1,
        "day": 24,
        "days": 24,
        "week": 168,
        "weeks": 168,
    }[unit]

    return {
        "valid": True,
        "duration": f"{amount} {unit}",
        "duration_value_hours": amount * multiplier,
    }


def validate_severity(text: str) -> Dict[str, object]:
    normalized = (text or "").strip().lower()
    allowed = {"mild", "moderate", "severe"}
    if normalized not in allowed:
        return {
            "valid": False,
            "error": "Please answer with exactly one severity level: mild, moderate, or severe.",
            "severity": None,
        }

    return {"valid": True, "severity": normalized}


def _body_part_condition_matches(text: str) -> List[str]:
    matches = []
    for body_part in BODY_PARTS:
        for condition in CONDITIONS:
            pattern = rf"\b{re.escape(body_part)}\s+{re.escape(condition)}\b"
            if re.search(pattern, text, re.IGNORECASE):
                matches.append(_normalize_body_part_condition(body_part, condition))
    return matches


def _normalize_body_part_condition(body_part: str, condition: str) -> str:
    if body_part in LIMB_PARTS:
        return "joint pain"
    if body_part == "chest":
        return "chest pain"
    if body_part == "stomach":
        return "abdominal pain"
    if body_part == "head":
        return "headache"
    if body_part == "back":
        return "back pain"
    return f"{body_part} {condition}"


def _dictionary_matches(text: str) -> List[str]:
    matches = []
    for normalized, variants in SYMPTOM_MAP.items():
        for variant in variants:
            if re.search(rf"\b{re.escape(variant)}\b", text, re.IGNORECASE):
                matches.append(normalized)
                break
    return matches


def dedupe_list(values: List[str]) -> List[str]:
    seen = []
    for value in values:
        cleaned = value.strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen
