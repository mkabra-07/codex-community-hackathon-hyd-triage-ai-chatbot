from typing import Any, Dict, List

from .context_engine import build_context, build_current_symptom_snapshot, build_history_summary
from .openai_service import generate_triage_explanation, normalize_symptom_with_llm
from .rules_engine import calculate_risk
from .session_store import Stage, append_message, reset_triage_state, update_profile, update_triage_state
from .validation import validate_duration, validate_severity, validate_symptoms


STAGE_LABELS = {
    Stage.SYMPTOM_COLLECTION: "Step 1/4: Symptoms",
    Stage.DURATION_COLLECTION: "Step 2/4: Duration",
    Stage.SEVERITY_COLLECTION: "Step 3/4: Severity",
    Stage.FOLLOW_UPS: "Step 4/4: Follow-up questions",
    Stage.TRIAGE_RESULT: "Final step: Triage result",
}

FOLLOW_UPS_BY_SYMPTOM = {
    "headache": [
        {
            "key": "headache_nausea",
            "question": "Have you also had nausea or vomiting with the headache?",
        },
        {
            "key": "headache_vision",
            "question": "Any vision changes, light sensitivity, or confusion with the headache?",
        },
    ],
    "fever": [
        {
            "key": "fever_temperature",
            "question": "What is the highest temperature you have measured, if any?",
        }
    ],
}


def handle_chat(
    user_input: str,
    session_key: str,
    session: Dict[str, Any],
    openai_client,
    model: str,
    patient_history_context: Dict[str, Any] | None = None,
    db_path: str | None = None,
    user_id: str | None = None,
):
    triage = session["triage"]

    if triage["stage"] == Stage.TRIAGE_RESULT:
        reset_triage_state(session_key)
        triage = session["triage"]

    if triage["stage"] == Stage.SYMPTOM_COLLECTION:
        return _handle_symptoms(user_input, session_key, session, openai_client, model)
    if triage["stage"] == Stage.DURATION_COLLECTION:
        return _handle_duration(user_input, session_key, session)
    if triage["stage"] == Stage.SEVERITY_COLLECTION:
        return _handle_severity(
            user_input, session_key, session, openai_client, model, patient_history_context, db_path, user_id
        )
    if triage["stage"] == Stage.FOLLOW_UPS:
        return _handle_follow_ups(
            user_input, session_key, session, openai_client, model, patient_history_context, db_path, user_id
        )

    return _build_final_response(
        session_key, session, openai_client, model, patient_history_context, db_path, user_id
    )


def build_initial_prompt(session: Dict[str, Any], patient_history_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    triage = session["triage"]
    return {
        "reply": "This is not medical advice. What symptoms are you experiencing?",
        "stage": triage["stage"],
        "progressLabel": STAGE_LABELS[triage["stage"]],
        "assessment": None,
        "debug": _debug_payload(session, patient_history_context),
    }


def _handle_symptoms(user_input: str, session_key: str, session: Dict[str, Any], openai_client, model: str):
    validation = validate_symptoms(
        user_input,
        llm_fallback=lambda text: normalize_symptom_with_llm(openai_client, model, text),
    )
    symptoms = validation["symptoms"]
    if not validation["valid"]:
        reply = f"This is not medical advice. {validation['error']}"
        append_message(session_key, "assistant", reply, {"stage": Stage.SYMPTOM_COLLECTION})
        return _response(reply, session)

    update_triage_state(
        session_key,
        {
            "symptoms": symptoms,
            "stage": Stage.DURATION_COLLECTION,
            "history_summary": [f"Symptoms reported: {', '.join(symptoms)}"],
        },
    )
    session["triage"] = update_triage_state(session_key)
    reply = (
        f"This is not medical advice. Recognized symptoms: {validation['recognized_symptom']}. "
        "How long have you been experiencing these symptoms?"
    )
    append_message(session_key, "assistant", reply, {"stage": Stage.DURATION_COLLECTION})
    return _response(reply, session)


def _handle_duration(user_input: str, session_key: str, session: Dict[str, Any]):
    validation = validate_duration(user_input)
    if not validation["valid"]:
        reply = f"This is not medical advice. {validation['error']}"
        append_message(session_key, "assistant", reply, {"stage": Stage.DURATION_COLLECTION})
        return _response(reply, session)

    duration = validation["duration"]
    update_triage_state(
        session_key,
        {
            "duration": duration,
            "duration_value_hours": validation["duration_value_hours"],
            "stage": Stage.SEVERITY_COLLECTION,
            "history_summary": session["triage"]["history_summary"] + [f"Duration: {duration}"],
        },
    )
    session["triage"] = update_triage_state(session_key)
    reply = "This is not medical advice. How severe is it right now: mild, moderate, or severe?"
    append_message(session_key, "assistant", reply, {"stage": Stage.SEVERITY_COLLECTION})
    return _response(reply, session)


def _handle_severity(
    user_input: str,
    session_key: str,
    session: Dict[str, Any],
    openai_client,
    model: str,
    patient_history_context: Dict[str, Any] | None,
    db_path: str | None,
    user_id: str | None,
):
    validation = validate_severity(user_input)
    severity = validation["severity"]
    if not validation["valid"]:
        reply = f"This is not medical advice. {validation['error']}"
        append_message(session_key, "assistant", reply, {"stage": Stage.SEVERITY_COLLECTION})
        return _response(reply, session)

    follow_ups = _build_follow_ups(session["triage"]["symptoms"], session["profile"])
    next_stage = Stage.FOLLOW_UPS if follow_ups else Stage.TRIAGE_RESULT
    summary = session["triage"]["history_summary"] + [f"Severity: {severity}"]
    update_triage_state(
        session_key,
        {
            "severity": severity,
            "stage": next_stage,
            "pending_follow_ups": follow_ups,
            "history_summary": summary,
        },
    )
    session["triage"] = update_triage_state(session_key)

    if follow_ups:
        question = follow_ups[0]["question"]
        reply = f"This is not medical advice. {question}"
        append_message(session_key, "assistant", reply, {"stage": Stage.FOLLOW_UPS})
        return _response(reply, session)

    return _build_final_response(
        session_key, session, openai_client, model, patient_history_context, db_path, user_id
    )


def _handle_follow_ups(
    user_input: str,
    session_key: str,
    session: Dict[str, Any],
    openai_client,
    model: str,
    patient_history_context: Dict[str, Any] | None,
    db_path: str | None,
    user_id: str | None,
):
    triage = session["triage"]
    pending = triage["pending_follow_ups"]
    current = pending[0] if pending else None

    if current:
        normalized_answer = _normalize_follow_up_answer(current, user_input)
        if not normalized_answer["valid"]:
            reply = f"This is not medical advice. {normalized_answer['error']}"
            append_message(session_key, "assistant", reply, {"stage": Stage.FOLLOW_UPS})
            return _response(reply, session, patient_history_context=patient_history_context)

        additional_answers = {
            **triage["additional_answers"],
            current["key"]: normalized_answer["summary_value"],
        }
        completed = triage["completed_follow_ups"] + [current["key"]]
        remaining = pending[1:]
        summary = triage["history_summary"] + [f"{current['key']}: {normalized_answer['summary_value']}"]
        next_stage = Stage.FOLLOW_UPS if remaining else Stage.TRIAGE_RESULT
        profile_updates = normalized_answer.get("profile_updates") or {}
        if profile_updates:
            session["profile"] = update_profile(session_key, profile_updates)
        update_triage_state(
            session_key,
            {
                "additional_answers": additional_answers,
                "completed_follow_ups": completed,
                "pending_follow_ups": remaining,
                "history_summary": summary,
                "stage": next_stage,
            },
        )
        session["triage"] = update_triage_state(session_key)

    if session["triage"]["pending_follow_ups"]:
        question = session["triage"]["pending_follow_ups"][0]["question"]
        reply = f"This is not medical advice. {question}"
        append_message(session_key, "assistant", reply, {"stage": Stage.FOLLOW_UPS})
        return _response(reply, session, patient_history_context=patient_history_context)

    return _build_final_response(
        session_key, session, openai_client, model, patient_history_context, db_path, user_id
    )


def _build_final_response(
    session_key: str,
    session: Dict[str, Any],
    openai_client,
    model: str,
    patient_history_context: Dict[str, Any] | None,
    db_path: str | None,
    user_id: str | None,
):
    triage = session["triage"]
    update_triage_state(session_key, {"stage": Stage.TRIAGE_RESULT})
    session["triage"] = update_triage_state(session_key)

    rules_result = calculate_risk(session["triage"])
    if rules_result["risk_level"] is None:
        reply = (
            "This is not medical advice. I couldn't recognize a valid medical symptom, "
            "so please start again by describing what you're feeling physically."
        )
        session["triage"] = reset_triage_state(session_key)
        append_message(session_key, "assistant", reply, {"stage": Stage.SYMPTOM_COLLECTION})
        return _response(reply, session, assessment=None)
    explanation = generate_triage_explanation(
        client=openai_client,
        model=model,
        context=_build_ai_context(session, patient_history_context, db_path, user_id),
        rules_result=rules_result,
    )
    assessment = {
        "risk_level": rules_result["risk_level"],
        "score": rules_result["score"],
        "reasoning": explanation["reasoning"],
        "summary": explanation["summary"],
        "next_steps": explanation["next_steps"],
    }
    session["triage"] = update_triage_state(session_key, {"last_result": assessment})
    reply = assessment["summary"]
    append_message(
        session_key,
        "assistant",
        reply,
        {"stage": Stage.TRIAGE_RESULT, "risk_level": assessment["risk_level"], "score": assessment["score"]},
    )
    return _response(reply, session, assessment=assessment, patient_history_context=patient_history_context)


def _build_follow_ups(symptoms: List[str], profile: Dict[str, Any]) -> List[Dict[str, str]]:
    follow_ups = []
    seen = set()
    for symptom in symptoms:
        for key, questions in FOLLOW_UPS_BY_SYMPTOM.items():
            if key in symptom:
                for question in questions:
                    if question["key"] not in seen:
                        seen.add(question["key"])
                        follow_ups.append(question)
    for question in _build_profile_follow_ups(profile):
        if question["key"] not in seen:
            seen.add(question["key"])
            follow_ups.append(question)
    return follow_ups


def _build_profile_follow_ups(profile: Dict[str, Any]) -> List[Dict[str, str]]:
    follow_ups = []
    if str(profile.get("age", "")).strip() == "":
        follow_ups.append(
            {
                "key": "profile_age",
                "question": "What is the patient's age?",
                "profile_field": "age",
            }
        )
    if str(profile.get("existing_conditions", "")).strip() == "":
        follow_ups.append(
            {
                "key": "profile_existing_conditions",
                "question": "Do you have any chronic conditions or take regular medications?",
                "profile_field": "existing_conditions",
            }
        )
    return follow_ups


def _normalize_follow_up_answer(current: Dict[str, str], user_input: str) -> Dict[str, Any]:
    text = user_input.strip()
    profile_field = current.get("profile_field")

    if profile_field == "age":
        if not text.isdigit() or int(text) <= 0:
            return {"valid": False, "error": "Please enter the age as a number in years."}
        age = int(text)
        return {"valid": True, "summary_value": str(age), "profile_updates": {"age": age}}

    if profile_field == "existing_conditions":
        normalized = text or "none"
        return {
            "valid": True,
            "summary_value": normalized,
            "profile_updates": {"existing_conditions": normalized},
        }

    return {"valid": True, "summary_value": text}


def _build_ai_context(
    session: Dict[str, Any],
    patient_history_context: Dict[str, Any] | None,
    db_path: str | None,
    user_id: str | None,
) -> Dict[str, Any]:
    generated_summary = (patient_history_context or {}).get("generated_summary")
    if isinstance(generated_summary, dict):
        history_summary = str(generated_summary.get("summary", "")).strip()
    else:
        history_summary = str(generated_summary or "").strip()
    if not history_summary and db_path and user_id:
        history_summary = build_history_summary(db_path, user_id, session["history"])

    current_message = build_current_symptom_snapshot(
        session["triage"],
        _latest_user_message(session["history"]),
    )
    return build_context(session["profile"], history_summary, current_message)


def _latest_user_message(history: List[Dict[str, Any]]) -> str:
    for item in reversed(history):
        if item.get("role") == "user":
            return str(item.get("content", ""))
    return ""


def _response(
    reply: str,
    session: Dict[str, Any],
    assessment=None,
    patient_history_context: Dict[str, Any] | None = None,
):
    stage = session["triage"]["stage"]
    return {
        "reply": reply,
        "assessment": assessment,
        "stage": stage,
        "progressLabel": STAGE_LABELS[stage],
        "debug": _debug_payload(session, patient_history_context),
    }


def _debug_payload(session: Dict[str, Any], patient_history_context: Dict[str, Any] | None = None):
    triage = session["triage"]
    return {
        "stage": triage["stage"],
        "symptoms": triage["symptoms"],
        "duration": triage["duration"],
        "duration_value_hours": triage.get("duration_value_hours"),
        "severity": triage["severity"],
        "additional_answers": triage["additional_answers"],
        "pending_follow_ups": [item["key"] for item in triage["pending_follow_ups"]],
        "last_result": triage["last_result"],
        "history_summary": (patient_history_context or {}).get("generated_summary"),
        "history_cached": (patient_history_context or {}).get("cached"),
        "raw_history": (patient_history_context or {}).get("raw_history"),
        "session_timeline": (patient_history_context or {}).get("session_timeline"),
    }
