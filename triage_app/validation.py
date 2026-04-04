import re
from datetime import datetime
from difflib import get_close_matches
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

CONDITIONS = ["pain", "ache", "swelling", "tightness", "burning", "hurts", "hurting"]
LIMB_PARTS = {"knee", "leg", "arm", "shoulder", "elbow"}

SYMPTOM_MAP = {
    "headache": ["headache", "head pain", "head hurts", "migraine"],
    "joint pain": [
        "knee pain",
        "elbow pain",
        "shoulder pain",
        "arm pain",
        "leg pain",
        "my knee hurts",
        "pain in knee",
        "shoulder ache",
        "joint pain",
    ],
    "swelling": ["swelling", "joint swelling", "knee swelling", "shoulder swelling"],
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

SEVERITY_RULES = {
    "mild": ["mild", "mild only", "slight", "slightly painful", "a little", "minor"],
    "moderate": ["moderate", "medium", "fairly bad", "noticeable"],
    "severe": ["severe", "very severe", "extreme", "really bad", "intense"],
}

DAY_WORDS = {
    "a day": 1,
    "one day": 1,
    "yesterday": 1,
    "couple of days": 2,
    "few days": 3,
    "several days": 5,
    "a week": 7,
    "one week": 7,
    "a month": 30,
    "one month": 30,
}

_NUMERIC_DURATION_PATTERN = re.compile(
    r"(?:for|about|around|roughly|approximately|since)?\s*(\d+)\s*(hour|hours|day|days|week|weeks|month|months)\b",
    re.IGNORECASE,
)

_ARTICLE_DURATION_PATTERN = re.compile(
    r"\b(a|an|one|couple of|few|several)\s+(hour|hours|day|days|week|weeks|month|months)\b",
    re.IGNORECASE,
)


def interpret_medical_input(text: str, llm_fallback=None) -> Dict[str, Any]:
    extracted = process_input(text, llm_fallback=llm_fallback)
    symptoms_result = extracted["symptoms"]
    duration_result = validate_duration(
        text,
        llm_fallback=llm_fallback,
        allow_ambiguous=True,
        extracted_data=extracted["extraction_output"],
    )
    severity_result = validate_severity(
        text,
        llm_fallback=llm_fallback,
        allow_ambiguous=True,
        extracted_data=extracted["extraction_output"],
    )
    return {
        "normalized": {
            "symptoms": symptoms_result.get("symptoms", []),
            "duration_days": duration_result.get("duration_days"),
            "severity": severity_result.get("severity"),
        },
        "response": _build_interpretation_reply(symptoms_result, duration_result, severity_result),
        "extraction_output": extracted["extraction_output"],
        "fallback_output": extracted["fallback_output"],
        "final_merged_result": extracted["final_merged_result"],
        "symptoms": symptoms_result,
        "duration": duration_result,
        "severity": severity_result,
    }


def process_input(text: str, llm_fallback=None) -> Dict[str, Any]:
    extraction_output = _normalize_extraction_output(llm_fallback(text) if llm_fallback else None)
    fallback_symptoms, fallback_confidence = extract_and_normalize_symptoms(text)
    symptoms_result = validate_and_normalize_symptom(
        text,
        llm_fallback=llm_fallback,
        extracted_data=extraction_output,
        fallback_result={
            "symptoms": fallback_symptoms,
            "confidence": fallback_confidence,
        },
    )
    return {
        "extraction_output": extraction_output,
        "fallback_output": {
            "symptoms": fallback_symptoms,
            "confidence": fallback_confidence,
        },
        "final_merged_result": {
            "symptoms": symptoms_result.get("symptoms", []),
            "confidence": symptoms_result.get("confidence", "low"),
        },
        "symptoms": symptoms_result,
    }


def validate_and_normalize_symptom(text: str, llm_fallback=None, extracted_data=None, fallback_result=None) -> Dict[str, Any]:
    extraction_output = _normalize_extraction_output(extracted_data)
    extracted_symptoms = extraction_output["symptoms"] if extraction_output["valid"] else []

    if fallback_result is None:
        fallback_symptoms, fallback_confidence = extract_and_normalize_symptoms(text)
    else:
        fallback_symptoms = dedupe_list(fallback_result.get("symptoms", []))
        fallback_confidence = fallback_result.get("confidence", "low")

    if not extracted_symptoms and llm_fallback and extracted_data is None:
        extraction_output = _normalize_extraction_output(llm_fallback(text))
        extracted_symptoms = extraction_output["symptoms"] if extraction_output["valid"] else []

    symptoms = dedupe_list(extracted_symptoms + fallback_symptoms)
    confidence = _merged_confidence(
        extraction_valid=bool(extracted_symptoms),
        fallback_confidence=fallback_confidence,
        merged_count=len(symptoms),
    )

    if not symptoms:
        return {
            "valid": False,
            "classification": "UNKNOWN",
            "confidence": "low",
            "error": "I couldn’t fully understand your symptoms. Can you describe them more clearly (e.g., pain, fever, cough)?",
            "symptoms": [],
            "recognized_symptom": None,
            "needs_confirmation": False,
            "debug": {
                "extraction_output": extraction_output,
                "fallback_output": {
                    "symptoms": fallback_symptoms,
                    "confidence": fallback_confidence,
                },
                "final_merged_result": {"symptoms": []},
            },
        }

    return {
        "valid": True,
        "classification": "VALID_SYMPTOM",
        "confidence": confidence,
        "symptoms": symptoms,
        "recognized_symptom": ", ".join(symptoms),
        "needs_confirmation": confidence == "medium",
        "confirmation_prompt": (
            f"I understood that as {', '.join(symptoms)}. Is that correct?"
            if confidence == "medium"
            else None
        ),
        "debug": {
            "extraction_output": extraction_output,
            "fallback_output": {
                "symptoms": fallback_symptoms,
                "confidence": fallback_confidence,
            },
            "final_merged_result": {
                "symptoms": symptoms,
                "confidence": confidence,
            },
        },
    }


def extract_and_normalize_symptoms(text: str) -> tuple[List[str], str]:
    normalized = (text or "").lower().strip()
    found: List[str] = []
    confidence = "high"

    found.extend(_body_part_condition_matches(normalized))
    found.extend(_dictionary_matches(normalized))

    if found:
        return dedupe_list(found), confidence

    fuzzy = _fuzzy_symptom_matches(normalized)
    if fuzzy:
        return fuzzy, "medium"

    return [], "low"


def validate_symptoms(text: str, llm_fallback=None) -> Dict[str, Any]:
    return validate_and_normalize_symptom(text, llm_fallback=llm_fallback)


def validate_duration(text: str, llm_fallback=None, allow_ambiguous: bool = False, extracted_data=None) -> Dict[str, Any]:
    normalized = (text or "").lower().strip()

    parsed = _parse_duration_rules(normalized)
    if parsed:
        return parsed

    extraction_output = _normalize_extraction_output(extracted_data)
    duration_days = extraction_output.get("duration_days")

    if duration_days is None and llm_fallback and extracted_data is None:
        fallback = _normalize_extraction_output(llm_fallback(text))
        duration_days = fallback.get("duration_days")

    if duration_days is not None:
        return {
            "valid": True,
            "confidence": "medium",
            "duration_days": int(duration_days),
            "duration": _duration_label(int(duration_days)),
            "duration_value_hours": int(duration_days) * 24,
            "needs_confirmation": True,
            "confirmation_prompt": f"About {_duration_label(int(duration_days))}, thanks. Is that correct?",
        }

    if allow_ambiguous:
        return {
            "valid": False,
            "confidence": "low",
            "duration": None,
            "duration_days": None,
            "duration_value_hours": None,
            "needs_confirmation": False,
        }

    return {
        "valid": False,
        "confidence": "low",
        "error": "Tell me roughly how long, for example a month, 2 weeks, few days, or since last Monday.",
        "duration": None,
        "duration_days": None,
        "duration_value_hours": None,
        "needs_confirmation": False,
    }


def validate_severity(text: str, llm_fallback=None, allow_ambiguous: bool = False, extracted_data=None) -> Dict[str, Any]:
    normalized = (text or "").strip().lower()

    for severity, phrases in SEVERITY_RULES.items():
        if normalized == severity or normalized in phrases or any(phrase in normalized for phrase in phrases):
            confidence = "high" if normalized == severity else "medium"
            return {
                "valid": True,
                "severity": severity,
                "confidence": confidence,
                "needs_confirmation": confidence == "medium",
                "confirmation_prompt": (
                    f"Got it - {severity} severity. Is that correct?" if confidence == "medium" else None
                ),
            }

    fuzzy = get_close_matches(normalized, [item for values in SEVERITY_RULES.values() for item in values], n=1, cutoff=0.78)
    if fuzzy:
        for severity, phrases in SEVERITY_RULES.items():
            if fuzzy[0] in phrases:
                return {
                    "valid": True,
                    "severity": severity,
                    "confidence": "medium",
                    "needs_confirmation": True,
                    "confirmation_prompt": f"Got it - {severity} severity. Is that correct?",
                }

    extraction_output = _normalize_extraction_output(extracted_data)
    severity = extraction_output.get("severity")

    if severity is None and llm_fallback and extracted_data is None:
        fallback = _normalize_extraction_output(llm_fallback(text))
        severity = fallback.get("severity")

    if severity in {"mild", "moderate", "severe"}:
        return {
            "valid": True,
            "severity": severity,
            "confidence": "medium",
            "needs_confirmation": True,
            "confirmation_prompt": f"Got it - {severity} severity. Is that correct?",
        }

    if allow_ambiguous:
        return {
            "valid": False,
            "severity": None,
            "confidence": "low",
            "needs_confirmation": False,
        }

    return {
        "valid": False,
        "confidence": "low",
        "error": "Describe the severity naturally, for example mild, mild only, slightly painful, moderate, or very severe.",
        "severity": None,
        "needs_confirmation": False,
    }


def _parse_duration_rules(text: str) -> Dict[str, Any] | None:
    exact_word = DAY_WORDS.get(text)
    if exact_word is not None:
        return _duration_payload(exact_word, "medium")

    for phrase, days in DAY_WORDS.items():
        if phrase in text:
            return _duration_payload(days, "medium")

    numeric = _NUMERIC_DURATION_PATTERN.search(text)
    if numeric:
        amount = int(numeric.group(1))
        unit = numeric.group(2).lower()
        days = _convert_to_days(amount, unit)
        return _duration_payload(days, "high", raw=f"{amount} {unit}")

    article = _ARTICLE_DURATION_PATTERN.search(text)
    if article:
        amount_word = article.group(1).lower()
        unit = article.group(2).lower()
        amount = {
            "a": 1,
            "an": 1,
            "one": 1,
            "couple of": 2,
            "few": 3,
            "several": 5,
        }[amount_word]
        days = _convert_to_days(amount, unit)
        return _duration_payload(days, "medium", raw=f"{amount_word} {unit}")

    since_days = _parse_since_phrase(text)
    if since_days is not None:
        return _duration_payload(since_days, "medium", raw=text)

    return None


def _parse_since_phrase(text: str) -> int | None:
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    match = re.search(r"since\s+last\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", text)
    if not match:
        return None

    target = weekdays.index(match.group(1))
    today = datetime.now()
    delta = (today.weekday() - target) % 7 or 7
    return delta


def _duration_payload(days: int, confidence: str, raw: str | None = None) -> Dict[str, Any]:
    return {
        "valid": True,
        "confidence": confidence,
        "duration_days": days,
        "duration": _duration_label(days),
        "duration_value_hours": days * 24,
        "raw": raw,
        "needs_confirmation": confidence == "medium",
        "confirmation_prompt": (
            f"About {_duration_label(days)}, thanks. Is that correct?" if confidence == "medium" else None
        ),
    }


def _convert_to_days(amount: int, unit: str) -> int:
    if unit.startswith("hour"):
        return max(1, round(amount / 24))
    if unit.startswith("day"):
        return amount
    if unit.startswith("week"):
        return amount * 7
    if unit.startswith("month"):
        return amount * 30
    return amount


def _duration_label(days: int) -> str:
    if days % 30 == 0 and days >= 30:
        months = days // 30
        return f"{months} month" if months == 1 else f"{months} months"
    if days % 7 == 0 and days >= 7:
        weeks = days // 7
        return f"{weeks} week" if weeks == 1 else f"{weeks} weeks"
    return f"{days} day" if days == 1 else f"{days} days"


def _body_part_condition_matches(text: str) -> List[str]:
    matches = []
    for body_part in BODY_PARTS:
        for condition in CONDITIONS:
            if re.search(rf"\b{re.escape(body_part)}\s+{re.escape(condition)}\b", text):
                matches.append(_normalize_body_part_condition(body_part, condition))
            if re.search(rf"\b{re.escape(condition)}\s+in\s+(my\s+)?{re.escape(body_part)}\b", text):
                matches.append(_normalize_body_part_condition(body_part, condition))
            if condition in {"hurts", "hurting"} and re.search(rf"\b(my\s+)?{re.escape(body_part)}\s+{re.escape(condition)}\b", text):
                matches.append(_normalize_body_part_condition(body_part, "pain"))
    return matches


def _normalize_body_part_condition(body_part: str, condition: str) -> str:
    if body_part in LIMB_PARTS:
        return "joint pain" if condition != "swelling" else "swelling"
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


def _fuzzy_symptom_matches(text: str) -> List[str]:
    variants = {variant: canonical for canonical, items in SYMPTOM_MAP.items() for variant in items}
    candidates = get_close_matches(text, list(variants.keys()), n=3, cutoff=0.78)
    return dedupe_list([variants[item] for item in candidates])


def _build_interpretation_reply(symptoms: Dict[str, Any], duration: Dict[str, Any], severity: Dict[str, Any]) -> str:
    parts = []
    if symptoms.get("valid"):
        parts.append(f"Recognized symptoms: {symptoms['recognized_symptom']}.")
    if duration.get("valid") and duration.get("duration_days") is not None:
        parts.append(f"About {duration['duration_days']} days.")
    if severity.get("valid") and severity.get("severity"):
        parts.append(f"Severity sounds {severity['severity']}.")
    return " ".join(parts)


def dedupe_list(values: List[str]) -> List[str]:
    seen = []
    for value in values:
        cleaned = value.strip().lower()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def _normalize_extraction_output(value) -> Dict[str, Any]:
    payload = value or {}
    symptoms = payload.get("symptoms") or payload.get("normalized") or []
    if isinstance(symptoms, str):
        symptoms = [symptoms]
    normalized_symptoms = dedupe_list([str(item).strip().lower() for item in symptoms if str(item).strip()])

    duration_days = payload.get("duration_days")
    try:
        duration_days = int(duration_days) if duration_days is not None else None
    except (TypeError, ValueError):
        duration_days = None

    severity = payload.get("severity")
    severity = str(severity).strip().lower() if severity is not None else None
    if severity not in {"mild", "moderate", "severe"}:
        severity = None

    return {
        "valid": bool(normalized_symptoms),
        "symptoms": normalized_symptoms,
        "duration_days": duration_days,
        "severity": severity,
    }


def _merged_confidence(extraction_valid: bool, fallback_confidence: str, merged_count: int) -> str:
    if extraction_valid and fallback_confidence == "high":
        return "high"
    if fallback_confidence == "high" and not extraction_valid:
        return "high"
    if extraction_valid or fallback_confidence == "medium":
        return "medium"
    return fallback_confidence or "low"
