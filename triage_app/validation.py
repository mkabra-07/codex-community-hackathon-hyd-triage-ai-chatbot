import re
from typing import Dict, List


SYMPTOM_PATTERNS = {
    "chest pain": [r"\bchest pain\b", r"\bchest tightness\b"],
    "breathing difficulty": [
        r"\bdifficulty breathing\b",
        r"\bbreathing difficulty\b",
        r"\bshortness of breath\b",
        r"\bcan't breathe\b",
        r"\bcannot breathe\b",
    ],
    "seizure": [r"\bseizure\b", r"\bseizures\b", r"\bconvulsion\b", r"\bconvulsions\b"],
    "headache": [r"\bheadache\b", r"\bhead pain\b", r"\bmigraine\b"],
    "fever": [r"\bfever\b", r"\bhigh temperature\b", r"\btemperature\b"],
    "vomiting": [r"\bvomiting\b", r"\bvomit\b", r"\bthrew up\b", r"\bthrowing up\b"],
    "cough": [r"\bcough\b", r"\bcoughing\b"],
    "fatigue": [r"\bfatigue\b", r"\btired\b", r"\bexhausted\b", r"\bweakness\b"],
    "cold": [r"\bcold\b", r"\brunny nose\b", r"\bnasal congestion\b", r"\bstuffy nose\b"],
}

_DURATION_PATTERN = re.compile(
    r"^\s*(\d+)\s*(hour|hours|day|days|week|weeks)\s*$",
    re.IGNORECASE,
)


def validate_and_normalize_symptom(text: str) -> Dict[str, object]:
    symptoms = extract_symptoms(text)
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


def extract_symptoms(text: str) -> List[str]:
    normalized = (text or "").lower().strip()
    found: List[str] = []

    for symptom, patterns in SYMPTOM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, normalized, re.IGNORECASE):
                found.append(symptom)
                break

    return found


def validate_symptoms(text: str) -> Dict[str, object]:
    return validate_and_normalize_symptom(text)


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
