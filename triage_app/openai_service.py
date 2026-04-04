import json
from io import BytesIO
from typing import Any, Dict

from openai import OpenAI

from .message_format import sanitize_assistant_text


TRIAGE_SYSTEM_PROMPT = """
You are a healthcare triage assistant.

Patient Profile:
Age: X
Conditions: X

Patient History Summary:
...

Current Symptoms:
...

Instructions:
- Do not repeat known questions
- Focus on new symptoms
- Be concise and safe

RULES:
- Do not classify the case yourself.
- The risk level and score are already determined by a deterministic rules engine.
- Use the provided risk level and rule hits to write a clear explanation and practical next steps.
- Do not diagnose.
- Return valid JSON only.

Return valid JSON only with this exact shape:
{
  "reasoning": "string",
  "summary": "string",
  "next_steps": ["string"]
}
""".strip()

SYMPTOM_NORMALIZATION_PROMPT = """
You classify whether user text describes a medical symptom.

Rules:
- Only mark valid if the input clearly describes a physical symptom or complaint.
- Normalize to short standard symptom labels.
- Support multi-word symptoms such as joint pain, abdominal pain, chest pain, breathing difficulty.
- If it is not a medical symptom, return valid false.

Return valid JSON only in this exact shape:
{
  "valid": true,
  "normalized": ["joint pain"]
}
""".strip()

MEDICAL_INPUT_EXTRACTION_PROMPT = """
Extract structured medical input from the user's message.

Return valid JSON only with this exact shape:
{
  "symptoms": [],
  "duration_days": null,
  "severity": null
}

Rules:
- Normalize symptoms into short canonical medical labels.
- Convert natural language durations into approximate days when needed.
- Normalize severity into one of: mild, moderate, severe.
- Leave any unknown field as null or [].
""".strip()


def create_openai_client(api_key: str):
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def transcribe_audio(client, audio_bytes: bytes, filename: str = "recording.webm") -> Dict[str, Any]:
    if client is None:
        raise ValueError("Speech transcription is not configured.")
    if not audio_bytes:
        raise ValueError("No audio received.")

    audio_file = BytesIO(audio_bytes)
    audio_file.name = filename

    response = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
    )

    text = str(getattr(response, "text", "") or "").strip()
    return {"text": text}


def normalize_symptom_with_llm(client, model: str, text: str) -> Dict[str, Any]:
    if client is None:
        return {"valid": False, "normalized": []}

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0,
        messages=[
            {"role": "system", "content": SYMPTOM_NORMALIZATION_PROMPT},
            {"role": "user", "content": f"Input: {text}"},
        ],
    )

    raw_content = response.choices[0].message.content or "{}"
    parsed = json.loads(raw_content)
    normalized = parsed.get("normalized", []) or []
    if isinstance(normalized, str):
        normalized = [normalized]

    return {
        "valid": bool(parsed.get("valid")),
        "normalized": [str(item).strip().lower() for item in normalized if str(item).strip()],
    }


def extract_structured_input_with_llm(client, model: str, text: str) -> Dict[str, Any]:
    if client is None:
        return {"symptoms": [], "duration_days": None, "severity": None}

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0,
        messages=[
            {"role": "system", "content": MEDICAL_INPUT_EXTRACTION_PROMPT},
            {"role": "user", "content": text},
        ],
    )

    raw_content = response.choices[0].message.content or "{}"
    parsed = json.loads(raw_content)
    symptoms = parsed.get("symptoms", []) or []
    if isinstance(symptoms, str):
        symptoms = [symptoms]

    severity = parsed.get("severity")
    if severity is not None:
        severity = str(severity).strip().lower()

    duration_days = parsed.get("duration_days")
    try:
        duration_days = int(duration_days) if duration_days is not None else None
    except (TypeError, ValueError):
        duration_days = None

    return {
        "symptoms": [str(item).strip().lower() for item in symptoms if str(item).strip()],
        "duration_days": duration_days,
        "severity": severity if severity in {"mild", "moderate", "severe"} else None,
    }


def generate_triage_explanation(
    client,
    model: str,
    context: Dict[str, Any],
    rules_result: Dict[str, Any],
) -> Dict[str, Any]:
    if client is None:
        return build_fallback_explanation(context, rules_result)

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "The rules engine already determined the final triage result.\n\n"
                    f"Risk level: {rules_result['risk_level']}\n"
                    f"Score: {rules_result['score']}\n"
                    f"Rule hits: {json.dumps(rules_result['rule_hits'])}\n\n"
                    "Patient Profile:\n"
                    f"Age: {context['profile'].get('age', 'unknown')}\n"
                    f"Conditions: {context['profile'].get('conditions', 'unknown')}\n\n"
                    "Patient History Summary:\n"
                    f"{context.get('history_summary', 'No relevant prior history available.')}\n\n"
                    "Current Symptoms:\n"
                    f"{context.get('current_message', '')}"
                ),
            },
        ],
    )

    raw_content = response.choices[0].message.content or "{}"
    parsed = json.loads(raw_content)
    return {
        "reasoning": parsed.get(
            "reasoning",
            "The final recommendation was based on the collected symptom details, severity, duration, and follow-up answers.",
        ),
        "summary": sanitize_assistant_text(
            parsed.get(
                "summary",
                "Based on the information collected, please follow the recommended next step below.",
            )
        ),
        "next_steps": parsed.get("next_steps", []) or ["Contact a clinician if you feel unsure or symptoms worsen."],
    }


def build_fallback_explanation(context: Dict[str, Any], rules_result: Dict[str, Any]) -> Dict[str, Any]:
    risk_level = rules_result["risk_level"]
    score = rules_result["score"]
    hits = ", ".join(rules_result["rule_hits"]) or "collected symptom details"
    symptoms = context.get("current_message") or "your symptoms"

    if risk_level == "EMERGENCY":
        summary = "Based on the information collected, this may need emergency attention."
        next_steps = [
            "Seek immediate medical attention now.",
            "Call local emergency services if symptoms are severe or worsening quickly.",
        ]
    elif risk_level == "URGENT":
        summary = "Based on the information collected, you should speak with a doctor within 24 hours."
        next_steps = [
            "Consult a doctor within 24 hours.",
            "Seek urgent care sooner if symptoms worsen or new red flags appear.",
        ]
    else:
        summary = "Based on the information collected, this does not currently sound like an emergency."
        next_steps = [
            "Monitor symptoms, rest, and stay hydrated.",
            "Contact a clinician if symptoms persist or worsen.",
        ]

    return {
        "reasoning": f"Risk level {risk_level} with score {score}, driven by: {hits}. Symptoms collected: {symptoms}.",
        "summary": summary,
        "next_steps": next_steps,
    }
