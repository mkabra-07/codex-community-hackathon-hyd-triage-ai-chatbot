import json
from typing import Any, Dict

from openai import OpenAI


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


def create_openai_client(api_key: str):
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


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
        "summary": parsed.get(
            "summary",
            "This is not medical advice. Based on the information collected, please follow the recommended next step below.",
        ),
        "next_steps": parsed.get("next_steps", []) or ["Contact a clinician if you feel unsure or symptoms worsen."],
    }


def build_fallback_explanation(context: Dict[str, Any], rules_result: Dict[str, Any]) -> Dict[str, Any]:
    risk_level = rules_result["risk_level"]
    score = rules_result["score"]
    hits = ", ".join(rules_result["rule_hits"]) or "collected symptom details"
    symptoms = context.get("current_message") or "your symptoms"

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
