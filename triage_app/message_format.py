import re
from typing import Any, Dict


GLOBAL_DISCLAIMER = "This is not medical advice."


def sanitize_assistant_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value

    value = value.replace(GLOBAL_DISCLAIMER, " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return value


def sanitize_assessment(assessment: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if assessment is None:
        return None

    cleaned = dict(assessment)
    if "summary" in cleaned:
        cleaned["summary"] = sanitize_assistant_text(str(cleaned["summary"]))
    return cleaned
