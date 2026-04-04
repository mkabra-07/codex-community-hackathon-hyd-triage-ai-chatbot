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
from .history_summary import summarize_patient_history
from .openai_service import create_openai_client
from .session_store import append_message, get_session
from .session_summaries import list_sessions_page, get_session_detail, maybe_refresh_session_summary
from .chat_flow import build_initial_prompt, handle_chat


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
            identifier = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            user = authenticate_user(app.config["SQLITE_PATH"], identifier, password)

            if not user:
                flash("Invalid email or password.", "error")
                return render_template("login.html", form_data={"email": identifier})

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
            "email": request.form.get("email", ""),
            "gender": request.form.get("gender", ""),
        }

        if request.method == "POST":
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not form_data["name"].strip() or not form_data["email"].strip() or not password:
                flash("Name, email, and password are required.", "error")
                return render_template("register.html", form_data=form_data)

            if password != confirm_password:
                flash("Passwords do not match.", "error")
                return render_template("register.html", form_data=form_data)

            try:
                user = create_user(
                    app.config["SQLITE_PATH"],
                    name=form_data["name"],
                    email=form_data["email"],
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
        session_key = f"{user['id']}:default"
        session_profile = _profile_payload(user)
        session_state = get_session(session_key, base_profile=session_profile)
        history_context = summarize_patient_history(
            user_id=user["id"],
            db_path=app.config["SQLITE_PATH"],
            client=openai_client,
            model=app.config["OPENAI_MODEL"],
            limit=50,
        )
        initial_sessions_page = list_sessions_page(
            db_path=app.config["SQLITE_PATH"],
            user_id=user["id"],
            client=openai_client,
            model=app.config["OPENAI_MODEL"],
            page=1,
            per_page=15,
        )
        return render_template(
            "chat.html",
            user=user,
            missing_fields=missing_profile_fields(user),
            profile=_profile_payload(user),
            initial_chat_state=build_initial_prompt(session_state, history_context),
            initial_history_context=history_context,
            initial_sessions_page=initial_sessions_page,
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

    @app.get("/history")
    @login_required
    def history():
        history_context = summarize_patient_history(
            user_id=g.user["id"],
            db_path=app.config["SQLITE_PATH"],
            client=openai_client,
            model=app.config["OPENAI_MODEL"],
            limit=50,
        )
        return jsonify(
            {
                "sessionTimeline": history_context.get("session_timeline", []),
                "summary": history_context.get("generated_summary", {}),
                "cached": history_context.get("cached", False),
            }
        )

    @app.get("/sessions")
    @login_required
    def sessions():
        page = request.args.get("page", default=1, type=int)
        per_page = request.args.get("per_page", default=15, type=int)
        payload = list_sessions_page(
            db_path=app.config["SQLITE_PATH"],
            user_id=g.user["id"],
            client=openai_client,
            model=app.config["OPENAI_MODEL"],
            page=page,
            per_page=per_page,
        )
        return jsonify(
            {
                "sessions": payload["sessions"],
                "page": payload["page"],
                "perPage": payload["per_page"],
                "total": payload["total"],
                "totalPages": payload["total_pages"],
                "hasMore": payload["has_more"],
            }
        )

    @app.get("/sessions/<session_id>")
    @login_required
    def session_detail(session_id: str):
        payload = get_session_detail(
            db_path=app.config["SQLITE_PATH"],
            user_id=g.user["id"],
            session_id=session_id,
            client=openai_client,
            model=app.config["OPENAI_MODEL"],
        )
        if payload is None:
            return jsonify({"error": "Session not found."}), 404
        return jsonify(payload)

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
        maybe_refresh_session_summary(
            db_path=app.config["SQLITE_PATH"],
            user_id=user["id"],
            session_id=session_id,
            client=openai_client,
            model=app.config["OPENAI_MODEL"],
        )
        history_context = summarize_patient_history(
            user_id=user["id"],
            db_path=app.config["SQLITE_PATH"],
            client=openai_client,
            model=app.config["OPENAI_MODEL"],
            limit=50,
        )

        try:
            response_payload = handle_chat(
                user_input=message,
                session_key=session_key,
                session=session_state,
                openai_client=openai_client,
                model=app.config["OPENAI_MODEL"],
                patient_history_context=history_context,
                db_path=app.config["SQLITE_PATH"],
                user_id=user["id"],
            )
            _sync_session_profile_to_user(user["id"], session_state["profile"])
            _persist_message(
                user["id"],
                session_id,
                "assistant",
                response_payload["reply"],
                response_payload["assessment"],
            )
            maybe_refresh_session_summary(
                db_path=app.config["SQLITE_PATH"],
                user_id=user["id"],
                session_id=session_id,
                client=openai_client,
                model=app.config["OPENAI_MODEL"],
                force=response_payload.get("stage") == "TRIAGE_RESULT",
            )
            return jsonify(_camelize_payload(response_payload))
        except Exception:
            fallback_reply = (
                "This is not medical advice. I'm having trouble completing the triage "
                "assessment right now, so the safest next step is to contact a doctor. "
                "If symptoms are severe or worsening, seek urgent care immediately."
            )
            fallback_assessment = {
                "risk_level": "URGENT",
                "reasoning": "The system could not complete the assessment safely.",
                "summary": fallback_reply,
                "next_steps": ["Contact a clinician for further assessment."],
            }
            append_message(session_key, "assistant", fallback_reply, {"stage": "TRIAGE_RESULT", "risk_level": "URGENT"})
            _persist_message(user["id"], session_id, "assistant", fallback_reply, fallback_assessment)
            maybe_refresh_session_summary(
                db_path=app.config["SQLITE_PATH"],
                user_id=user["id"],
                session_id=session_id,
                client=openai_client,
                model=app.config["OPENAI_MODEL"],
                force=True,
            )
            return (
                jsonify(
                    {
                        "reply": fallback_reply,
                        "assessment": fallback_assessment,
                        "stage": "TRIAGE_RESULT",
                        "progressLabel": "Final step: Triage result",
                        "debug": {
                            **session_state["triage"],
                            "history_summary": history_context.get("generated_summary"),
                            "raw_history": history_context.get("raw_history"),
                        },
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
        "stage": payload.get("stage"),
        "progressLabel": payload.get("progressLabel"),
        "debug": payload.get("debug"),
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


def _sync_session_profile_to_user(user_id: str, profile: Dict[str, object]) -> None:
    age = profile.get("age")
    existing_conditions = _conditions_list(profile.get("existing_conditions"))
    if age in ("", None) and not existing_conditions:
        return

    update_user_profile(
        current_app.config["SQLITE_PATH"],
        user_id,
        age=int(age) if age not in ("", None) else None,
        existing_conditions=existing_conditions if existing_conditions else None,
    )
