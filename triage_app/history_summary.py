import json
from typing import Any, Dict, List

from .database import (
    fetch_recent_conversations,
    get_cached_history_summary,
    get_latest_conversation_id,
    upsert_history_summary,
)


HISTORY_SUMMARY_PROMPT = """
You are a medical assistant. Summarize patient history into key medical insights:
- chronic issues
- recurring symptoms
- risk patterns
Keep it concise and structured.

Only use information explicitly present in the conversation history.
Do not hallucinate or infer medical conditions that are not stated.

Return valid JSON only in this shape:
{
  "chronic_conditions": [],
  "recent_symptoms": [],
  "risk_patterns": [],
  "summary": ""
}
""".strip()


def summarize_patient_history(
    *,
    user_id: str,
    db_path: str,
    client,
    model: str,
    limit: int = 30,
) -> Dict[str, Any]:
    latest_message_id = get_latest_conversation_id(db_path, user_id)
    cached = get_cached_history_summary(db_path, user_id)

    if cached and cached["last_message_id"] == latest_message_id:
        return {
            "cached": True,
            "raw_history": fetch_recent_conversations(db_path, user_id, limit),
            "generated_summary": cached["summary"],
            "session_timeline": build_session_timeline(fetch_recent_conversations(db_path, user_id, limit)),
        }

    raw_history = fetch_recent_conversations(db_path, user_id, limit)
    if not raw_history:
        summary = {
            "chronic_conditions": [],
            "recent_symptoms": [],
            "risk_patterns": [],
            "summary": "No prior conversation history available.",
        }
        upsert_history_summary(db_path, user_id, latest_message_id, summary)
        return {
            "cached": False,
            "raw_history": [],
            "generated_summary": summary,
            "session_timeline": [],
        }

    summary = (
        _summarize_with_llm(raw_history, client, model)
        if client is not None
        else _fallback_summary(raw_history)
    )

    upsert_history_summary(db_path, user_id, latest_message_id, summary)
    return {
        "cached": False,
        "raw_history": raw_history,
        "generated_summary": summary,
        "session_timeline": build_session_timeline(raw_history),
    }


def _summarize_with_llm(raw_history: List[Dict[str, Any]], client, model: str) -> Dict[str, Any]:
    transcript = "\n".join(
        f"{item['role']}: {item['message']}" for item in raw_history
    )

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0.1,
        messages=[
            {"role": "system", "content": HISTORY_SUMMARY_PROMPT},
            {
                "role": "user",
                "content": f"Conversation history:\n{transcript}",
            },
        ],
    )

    raw_content = response.choices[0].message.content or "{}"
    parsed = json.loads(raw_content)
    return {
        "chronic_conditions": parsed.get("chronic_conditions", []) or [],
        "recent_symptoms": parsed.get("recent_symptoms", []) or [],
        "risk_patterns": parsed.get("risk_patterns", []) or [],
        "summary": parsed.get("summary", "History summary generated from prior conversations."),
    }


def _fallback_summary(raw_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    chronic_conditions = []
    recent_symptoms = []
    risk_patterns = []
    combined = " ".join(item["message"].lower() for item in raw_history)

    for condition in ["asthma", "diabetes", "hypertension", "migraine"]:
        if condition in combined and condition not in chronic_conditions:
            chronic_conditions.append(condition)

    for symptom in ["fever", "headache", "cough", "chest pain", "fatigue", "vomiting"]:
        if symptom in combined and symptom not in recent_symptoms:
            recent_symptoms.append(symptom)

    if "repeat" in combined or "again" in combined:
        risk_patterns.append("possible recurring symptoms mentioned")
    if "worsening" in combined or "worse" in combined:
        risk_patterns.append("symptoms have worsened in prior chats")

    summary_text = " ".join(
        filter(
            None,
            [
                f"Chronic conditions noted: {', '.join(chronic_conditions)}." if chronic_conditions else "",
                f"Recent symptoms discussed: {', '.join(recent_symptoms)}." if recent_symptoms else "",
                f"Risk patterns: {', '.join(risk_patterns)}." if risk_patterns else "",
            ],
        )
    ) or "No major recurring history patterns found in prior chats."

    return {
        "chronic_conditions": chronic_conditions,
        "recent_symptoms": recent_symptoms,
        "risk_patterns": risk_patterns,
        "summary": summary_text,
    }


def build_session_timeline(raw_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in raw_history:
        session_id = item["session_id"]
        grouped.setdefault(
            session_id,
            {
                "session_id": session_id,
                "started_at": item["created_at"],
                "ended_at": item["created_at"],
                "message_count": 0,
                "messages": [],
            },
        )
        grouped[session_id]["ended_at"] = item["created_at"]
        grouped[session_id]["message_count"] += 1
        grouped[session_id]["messages"].append(
            {
                "id": item["id"],
                "role": item["role"],
                "message": item["message"],
                "created_at": item["created_at"],
            }
        )

    return list(grouped.values())
