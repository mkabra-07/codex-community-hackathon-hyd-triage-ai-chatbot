from typing import Dict, List


CRITICAL_SYMPTOMS = {"chest pain", "breathing difficulty", "seizure"}
MODERATE_SYMPTOMS = {"fever", "headache", "vomiting", "joint pain", "abdominal pain", "back pain"}
MINOR_SYMPTOMS = {"cold", "cough", "fatigue"}


def calculate_risk(data: Dict[str, object]) -> Dict[str, object]:
    symptoms = [str(item).lower() for item in data.get("symptoms", [])]
    severity = str(data.get("severity") or "").lower()
    duration_hours = int(data.get("duration_value_hours") or 0)

    if not symptoms:
        return {
            "risk_level": None,
            "score": 0,
            "rule_hits": ["no valid symptom available"],
        }

    if any(symptom in CRITICAL_SYMPTOMS for symptom in symptoms):
        return {
            "risk_level": "EMERGENCY",
            "score": 999,
            "rule_hits": ["critical symptom present"],
        }

    score = 0
    hits: List[str] = []

    severity_points = {"mild": 1, "moderate": 2, "severe": 3}.get(severity, 0)
    score += severity_points
    if severity_points:
        hits.append(f"severity={severity} (+{severity_points})")

    symptom_weights = {"critical": 4, "moderate": 2, "minor": 1}

    for symptom in symptoms:
        category = _symptom_category(symptom)
        if category == "critical":
            score += symptom_weights["critical"]
            hits.append(f"critical symptom={symptom} (+4)")
            if duration_hours > 24:
                score += 2
                hits.append(f"{symptom} lasting > 1 day (+2)")
        elif category == "moderate":
            score += symptom_weights["moderate"]
            hits.append(f"moderate symptom={symptom} (+2)")
            duration_bonus = _moderate_duration_bonus(symptom, severity, duration_hours, len(symptoms))
            score += duration_bonus
            if duration_bonus:
                hits.append(f"{symptom} duration adjustment (+{duration_bonus})")
        elif category == "minor":
            score += symptom_weights["minor"]
            hits.append(f"minor symptom={symptom} (+1)")
            if duration_hours > 120:
                score += 1
                hits.append(f"{symptom} lasting > 5 days (+1)")

    if score >= 6:
        risk_level = "EMERGENCY"
    elif score >= 4:
        risk_level = "URGENT"
    else:
        risk_level = "LOW"

    return {
        "risk_level": risk_level,
        "score": score,
        "rule_hits": hits,
    }


def _symptom_category(symptom: str) -> str | None:
    if symptom in CRITICAL_SYMPTOMS:
        return "critical"
    if symptom in MODERATE_SYMPTOMS:
        return "moderate"
    if symptom in MINOR_SYMPTOMS:
        return "minor"
    return None


def _moderate_duration_bonus(symptom: str, severity: str, duration_hours: int, symptom_count: int) -> int:
    if duration_hours <= 72:
        return 0

    if symptom == "headache" and severity == "mild" and symptom_count == 1:
        return 0

    return 2
