from functools import wraps
from typing import Dict, List

from flask import (
    Flask,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .database import (
    authenticate_user,
    create_user,
    get_user_by_id,
    missing_profile_fields,
    persist_message,
    update_user_profile,
)
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

    @app.before_request
    def load_current_user():
        user_id = session.get("user_id")
        g.user = get_user_by_id(app.config["SQLITE_PATH"], user_id) if user_id else None

    @app.get("/")
    def root():
        if g.user:
            return redirect(url_for("chat_page"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("chat_page"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "")
            user = authenticate_user(app.config["SQLITE_PATH"], name, password)

            if not user:
                flash("Invalid name or password.", "error")
                return render_template("login.html", form_data={"name": name})

            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("chat_page"))

        return render_template("login.html", form_data={})

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("chat_page"))

        form_data = {
            "name": request.form.get("name", ""),
            "gender": request.form.get("gender", ""),
        }

        if request.method == "POST":
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not form_data["name"].strip() or not password:
                flash("Name and password are required.", "error")
                return render_template("register.html", form_data=form_data)

            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template("register.html", form_data=form_data)

            try:
                user = create_user(
                    app.config["SQLITE_PATH"],
                    name=form_data["name"],
                    password=password,
                    gender=form_data["gender"],
                )
            except ValueError as error:
                flash(str(error), "error")
                return render_template("register.html", form_data=form_data)

            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("chat_page"))

        return render_template("register.html", form_data=form_data)

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/chat")
    @login_required
    def chat_page():
        user = g.user
        return render_template(
            "chat.html",
            user=user,
            missing_fields=missing_profile_fields(user),
            profile=_profile_payload(user),
        )

    @app.post("/profile")
    @login_required
    def update_profile_route():
        payload = request.get_json(silent=True) or {}

        try:
            age = _parse_optional_int(payload.get("age"))
            height = _parse_optional_float(payload.get("height"))
            weight = _parse_optional_float(payload.get("weight"))
            gender = (payload.get("gender") or "").strip() or None
            existing_conditions = _conditions_list(payload.get("existing_conditions"))

            user = update_user_profile(
                app.config["SQLITE_PATH"],
                g.user["id"],
                age=age,
                gender=gender,
                height=height,
                weight=weight,
                existing_conditions=existing_conditions,
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

        return jsonify(
            {
                "user": _safe_user(user),
                "missingFields": missing_profile_fields(user),
            }
        )

    @app.get("/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "sqlite_enabled": True,
                "ai_configured": bool(app.config["OPENAI_API_KEY"]),
            }
        )

    @app.post("/chat")
    @login_required
    def chat():
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("sessionId", "").strip()
        message = payload.get("message", "").strip()

        if not session_id or not message:
            return jsonify({"error": "sessionId and message are required."}), 400

        user = g.user
        session_key = f"{user['id']}:{session_id}"
        session_profile = _profile_payload(user)
        session_state = get_session(session_key, base_profile=session_profile)

        append_message(session_key, "user", message)
        _persist_message(user["id"], session_id, "user", message)

        emergency = detect_emergency_signals(message)
        if emergency["is_emergency"]:
            response_payload = build_emergency_response(session_key, emergency["matches"])
            _persist_message(
                user["id"],
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
                history=session_state["history"],
                profile=session_state["profile"],
                latest_message=message,
            )
            profile = merge_assessment_into_profile(session_key, assessment)
            response_payload = build_assistant_reply(session_key, assessment, profile)
            _persist_message(
                user["id"],
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
            append_message(session_key, "assistant", fallback_reply, {"risk_level": "URGENT"})
            _persist_message(user["id"], session_id, "assistant", fallback_reply, fallback_assessment)
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


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def _persist_message(user_id: str, session_id: str, role: str, message: str, metadata=None) -> None:
    persist_message(current_app.config["SQLITE_PATH"], user_id, session_id, role, message, metadata)


def _camelize_payload(payload):
    return {
        "reply": payload["reply"],
        "assessment": payload["assessment"],
        "followUpQuestions": payload.get("follow_up_questions", []),
    }


def _parse_optional_int(value):
    if value in (None, ""):
        return None
    return int(value)


def _parse_optional_float(value):
    if value in (None, ""):
        return None
    return float(value)


def _conditions_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).split(",")
    return [item.strip() for item in raw_items if str(item).strip()]


def _safe_user(user: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": user["id"],
        "name": user["name"],
        "age": user["age"],
        "gender": user["gender"],
        "height": user["height"],
        "weight": user["weight"],
        "existing_conditions": user["existing_conditions"],
    }


def _profile_payload(user: Dict[str, object]) -> Dict[str, object]:
    return {
        "age": user.get("age") if user.get("age") is not None else "",
        "gender": user.get("gender") or "",
        "height": user.get("height") if user.get("height") is not None else "",
        "weight": user.get("weight") if user.get("weight") is not None else "",
        "existing_conditions": ", ".join(user.get("existing_conditions") or []),
    }
