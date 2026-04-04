from flask import Flask, current_app, jsonify, render_template, request

from .database import persist_message
from .openai_service import create_openai_client, extract_triage_assessment
from .session_store import append_message, get_session
from .triage_engine import (
    build_assistant_reply,
    build_emergency_response,
    detect_emergency_signals,
    merge_assessment_into_profile,
)


def register_routes(app: Flask) -> None:
    openai_client = create_openai_client(app.config["OPENAI_API_KEY"])

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "sqlite_enabled": app.config["ENABLE_SQLITE"],
                "ai_configured": bool(app.config["OPENAI_API_KEY"]),
            }
        )

    @app.post("/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("sessionId", "").strip()
        message = payload.get("message", "").strip()

        if not session_id or not message:
            return jsonify({"error": "sessionId and message are required."}), 400

        session = get_session(session_id)
        append_message(session_id, "user", message)
        _persist_if_enabled(session_id, "user", message)

        emergency = detect_emergency_signals(message)
        if emergency["is_emergency"]:
            response_payload = build_emergency_response(session_id, emergency["matches"])
            _persist_if_enabled(
                session_id,
                "assistant",
                response_payload["reply"],
                response_payload["assessment"],
            )
            return jsonify(_camelize_payload(response_payload))

        try:
            assessment = extract_triage_assessment(
                client=openai_client,
                model=app.config["OPENAI_MODEL"],
                history=session["history"],
                profile=session["profile"],
                latest_message=message,
            )
            profile = merge_assessment_into_profile(session_id, assessment)
            response_payload = build_assistant_reply(session_id, assessment, profile)
            _persist_if_enabled(
                session_id,
                "assistant",
                response_payload["reply"],
                response_payload["assessment"],
            )
            return jsonify(_camelize_payload(response_payload))
        except Exception:
            fallback_reply = (
                "This is not medical advice. I'm having trouble completing the triage "
                "assessment right now, so the safest next step is to contact a doctor. "
                "If symptoms are severe or worsening, seek urgent care immediately."
            )
            fallback_assessment = {
                "symptoms": [],
                "risk_level": "URGENT",
                "reasoning": "The system could not complete the assessment safely.",
                "next_steps": ["Contact a clinician for further assessment."],
            }
            append_message(session_id, "assistant", fallback_reply, {"risk_level": "URGENT"})
            _persist_if_enabled(session_id, "assistant", fallback_reply, fallback_assessment)
            return (
                jsonify(
                    {
                        "reply": fallback_reply,
                        "assessment": fallback_assessment,
                        "followUpQuestions": [],
                    }
                ),
                500,
            )


def _persist_if_enabled(session_id: str, role: str, message: str, metadata=None) -> None:
    if current_app.config["ENABLE_SQLITE"]:
        persist_message(current_app.config["SQLITE_PATH"], session_id, role, message, metadata)


def _camelize_payload(payload):
    return {
        "reply": payload["reply"],
        "assessment": payload["assessment"],
        "followUpQuestions": payload.get("follow_up_questions", []),
    }
