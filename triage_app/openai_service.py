import json
from typing import Any, Dict, List

from openai import OpenAI


TRIAGE_SYSTEM_PROMPT = """
You are a structured medical triage assistant.

RULES:
- Do not classify the case yourself.
- The risk level and score are already determined by a deterministic rules engine.
- Use the provided risk level and rule hits to write a clear explanation and practical next steps.
- Do not diagnose.
- Be concise, cautious, and patient-friendly.

Return valid JSON only with this exact shape:
{
  "reasoning": "string",
  "summary": "string",
  "next_steps": ["string"]
}
""".strip()


def create_openai_client(api_key: str):
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def generate_triage_explanation(
    client,
    model: str,
    profile: Dict[str, Any],
    triage_state: Dict[str, Any],
    history: List[Dict[str, Any]],
    rules_result: Dict[str, Any],
) -> Dict[str, Any]:
    if client is None:
        return build_fallback_explanation(profile, triage_state, rules_result)

    conversation_text = "\n".join(
        f"{entry['role']}: {entry['content']}" for entry in history[-12:]
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
                    "The rules engine already determined the final triage result.\n\n"
                    f"Risk level: {rules_result['risk_level']}\n"
                    f"Score: {rules_result['score']}\n"
                    f"Rule hits: {json.dumps(rules_result['rule_hits'])}\n\n"
                    "Patient profile:\n"
                    f"Age: {profile.get('age', 'unknown')}\n"
                    f"Gender: {profile.get('gender', 'unknown')}\n"
                    f"Height: {profile.get('height', 'unknown')}\n"
                    f"Weight: {profile.get('weight', 'unknown')}\n"
                    f"Conditions: {profile.get('existing_conditions', 'unknown')}\n\n"
                    "Collected triage data:\n"
                    f"{json.dumps(triage_state, indent=2, default=str)}\n\n"
                    "Conversation summary:\n"
                    f"{conversation_text}"
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
        "summary": parsed.get(
            "summary",
            "This is not medical advice. Based on the information collected, please follow the recommended next step below.",
        ),
        "next_steps": parsed.get("next_steps", []) or ["Contact a clinician if you feel unsure or symptoms worsen."],
    }


def build_fallback_explanation(
    profile: Dict[str, Any], triage_state: Dict[str, Any], rules_result: Dict[str, Any]
) -> Dict[str, Any]:
    risk_level = rules_result["risk_level"]
    score = rules_result["score"]
    hits = ", ".join(rules_result["rule_hits"]) or "collected symptom details"
    symptoms = ", ".join(triage_state.get("symptoms", [])) or "your symptoms"

    if risk_level == "EMERGENCY":
        summary = (
            "This is not medical advice. Based on the information collected, this may need emergency attention."
        )
        next_steps = [
            "Seek immediate medical attention now.",
            "Call local emergency services if symptoms are severe or worsening quickly.",
        ]
    elif risk_level == "URGENT":
        summary = (
            "This is not medical advice. Based on the information collected, you should speak with a doctor within 24 hours."
        )
        next_steps = [
            "Consult a doctor within 24 hours.",
            "Seek urgent care sooner if symptoms worsen or new red flags appear.",
        ]
    else:
        summary = (
            "This is not medical advice. Based on the information collected, this does not currently sound like an emergency."
        )
        next_steps = [
            "Monitor symptoms, rest, and stay hydrated.",
            "Contact a clinician if symptoms persist or worsen.",
        ]

    return {
        "reasoning": f"Risk level {risk_level} with score {score}, driven by: {hits}. Symptoms collected: {symptoms}.",
        "summary": summary,
        "next_steps": next_steps,
    }
