import json
from typing import Any, Dict, List

from openai import OpenAI


TRIAGE_SYSTEM_PROMPT = """
You are a healthcare triage assistant for a patient-facing chatbot.
Your job is to assess urgency and recommend next steps, not diagnose.
You must be cautious, conservative, and prioritize safety over completeness.
Never provide a diagnosis. If uncertain, prefer escalation to a clinician.
Ask focused follow-up questions when key information is missing.
Use the patient profile provided to you in context. Do not ask again for profile details that are already known.

You must return valid JSON only with this exact shape:
{
  "symptoms": ["string"],
  "risk_level": "EMERGENCY|URGENT|LOW|UNKNOWN",
  "reasoning": "string",
  "next_steps": ["string"],
  "collected_facts": {
    "duration": "string",
    "severity": "mild|moderate|severe|unknown|string",
    "age": "string",
    "existing_conditions": "string"
  }
}
""".strip()


def create_openai_client(api_key: str):
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def extract_triage_assessment(
    client, model: str, history: List[Dict[str, Any]], profile: Dict[str, Any], latest_message: str
) -> Dict[str, Any]:
    if client is None:
        return build_fallback_assessment(profile, latest_message)

    conversation_text = "\n".join(
        f"{entry['role']}: {entry['content']}" for entry in history[-8:]
    )

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Patient profile:\n"
                    f"Age: {profile.get('age', 'unknown')}\n"
                    f"Gender: {profile.get('gender', 'unknown')}\n"
                    f"Height: {profile.get('height', 'unknown')}\n"
                    f"Weight: {profile.get('weight', 'unknown')}\n"
                    f"Conditions: {profile.get('existing_conditions', 'unknown')}\n\n"
                    "Existing collected facts:\n"
                    f"{json.dumps(profile, indent=2)}\n\n"
                    "Recent conversation:\n"
                    f"{conversation_text}\n\n"
                    "Latest user message:\n"
                    f"{latest_message}"
                ),
            },
        ],
    )

    raw_content = response.choices[0].message.content or "{}"
    parsed = json.loads(raw_content)

    return {
        "symptoms": parsed.get("symptoms", []) or [],
        "risk_level": parsed.get("risk_level", "UNKNOWN"),
        "reasoning": parsed.get(
            "reasoning", "Insufficient information to assess urgency confidently."
        ),
        "next_steps": parsed.get("next_steps", []) or [],
        "collected_facts": {
            "duration": parsed.get("collected_facts", {}).get("duration", ""),
            "severity": parsed.get("collected_facts", {}).get("severity", ""),
            "age": parsed.get("collected_facts", {}).get("age", ""),
            "existing_conditions": parsed.get("collected_facts", {}).get(
                "existing_conditions", ""
            ),
        },
    }


def build_fallback_assessment(profile: Dict[str, Any], latest_message: str) -> Dict[str, Any]:
    normalized = latest_message.lower()
    symptoms = []

    symptom_map = {
        "fever": "fever",
        "cough": "cough",
        "headache": "headache",
        "vomit": "vomiting",
        "pain": "pain",
    }

    for needle, label in symptom_map.items():
        if needle in normalized:
            symptoms.append(label)

    return {
        "symptoms": symptoms,
        "risk_level": "UNKNOWN",
        "reasoning": (
            "The AI assessment service is unavailable, so the system is collecting "
            "information conservatively before recommending a clinician review."
        ),
        "next_steps": [
            "Answer the follow-up questions so the system can suggest an appropriate level of care.",
            "If symptoms worsen or you feel unsafe, contact a clinician urgently.",
        ],
        "collected_facts": {
            "duration": profile.get("duration", ""),
            "severity": profile.get("severity", ""),
            "age": profile.get("age", ""),
            "existing_conditions": profile.get("existing_conditions", ""),
        },
    }
