from typing import Any, Dict, List

from .openai_service import generate_triage_explanation
from .rules_engine import calculate_risk
from .session_store import Stage, append_message, reset_triage_state, update_triage_state
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


def handle_chat(user_input: str, session_key: str, session: Dict[str, Any], openai_client, model: str):
    triage = session["triage"]

    if triage["stage"] == Stage.TRIAGE_RESULT:
        reset_triage_state(session_key)
        triage = session["triage"]

    if triage["stage"] == Stage.SYMPTOM_COLLECTION:
        return _handle_symptoms(user_input, session_key, session)
    if triage["stage"] == Stage.DURATION_COLLECTION:
        return _handle_duration(user_input, session_key, session)
    if triage["stage"] == Stage.SEVERITY_COLLECTION:
        return _handle_severity(user_input, session_key, session, openai_client, model)
    if triage["stage"] == Stage.FOLLOW_UPS:
        return _handle_follow_ups(user_input, session_key, session, openai_client, model)

    return _build_final_response(session_key, session, openai_client, model)


def build_initial_prompt(session: Dict[str, Any]) -> Dict[str, Any]:
    triage = session["triage"]
    return {
        "reply": "This is not medical advice. What symptoms are you experiencing?",
        "stage": triage["stage"],
        "progressLabel": STAGE_LABELS[triage["stage"]],
        "assessment": None,
        "debug": _debug_payload(session),
    }


def _handle_symptoms(user_input: str, session_key: str, session: Dict[str, Any]):
    validation = validate_symptoms(user_input)
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
        f"This is not medical advice. Recognized symptom: {validation['recognized_symptom']}. "
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


def _handle_severity(user_input: str, session_key: str, session: Dict[str, Any], openai_client, model: str):
    validation = validate_severity(user_input)
    severity = validation["severity"]
    if not validation["valid"]:
        reply = f"This is not medical advice. {validation['error']}"
        append_message(session_key, "assistant", reply, {"stage": Stage.SEVERITY_COLLECTION})
        return _response(reply, session)

    follow_ups = _build_follow_ups(session["triage"]["symptoms"])
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

    return _build_final_response(session_key, session, openai_client, model)


def _handle_follow_ups(user_input: str, session_key: str, session: Dict[str, Any], openai_client, model: str):
    triage = session["triage"]
    pending = triage["pending_follow_ups"]
    current = pending[0] if pending else None

    if current:
        additional_answers = {
            **triage["additional_answers"],
            current["key"]: user_input.strip(),
        }
        completed = triage["completed_follow_ups"] + [current["key"]]
        remaining = pending[1:]
        summary = triage["history_summary"] + [f"{current['key']}: {user_input.strip()}"]
        next_stage = Stage.FOLLOW_UPS if remaining else Stage.TRIAGE_RESULT
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
        return _response(reply, session)

    return _build_final_response(session_key, session, openai_client, model)


def _build_final_response(session_key: str, session: Dict[str, Any], openai_client, model: str):
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
        profile=session["profile"],
        triage_state=session["triage"],
        history=session["history"],
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
    return _response(reply, session, assessment=assessment)


def _build_follow_ups(symptoms: List[str]) -> List[Dict[str, str]]:
    follow_ups = []
    seen = set()
    for symptom in symptoms:
        for key, questions in FOLLOW_UPS_BY_SYMPTOM.items():
            if key in symptom:
                for question in questions:
                    if question["key"] not in seen:
                        seen.add(question["key"])
                        follow_ups.append(question)
    return follow_ups


def _response(reply: str, session: Dict[str, Any], assessment=None):
    stage = session["triage"]["stage"]
    return {
        "reply": reply,
        "assessment": assessment,
        "stage": stage,
        "progressLabel": STAGE_LABELS[stage],
        "debug": _debug_payload(session),
    }


def _debug_payload(session: Dict[str, Any]):
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
    }
