from functools import wraps
from datetime import datetime, timezone
from typing import Dict, List
from uuid import uuid4

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

from .chat_flow import build_initial_prompt, handle_chat
from .database import (
    authenticate_user,
    create_user_session,
    create_user,
    end_user_session,
    expire_user_session_if_idle,
    get_user_by_id,
    get_user_session,
    missing_profile_fields,
    persist_message,
    touch_user_session,
    update_user_profile,
)
from .history_summary import summarize_patient_history
from .message_format import sanitize_assessment, sanitize_assistant_text
from .openai_service import create_openai_client, transcribe_audio
from .session_store import append_message, get_session
from .session_summaries import get_session_detail, list_sessions_page, maybe_refresh_session_summary


def register_routes(app: Flask) -> None:
    openai_client = create_openai_client(app.config["OPENAI_API_KEY"])
    app.extensions["openai_client"] = openai_client

    @app.before_request
    def load_current_user():
        user_id = session.get("user_id")
        auth_session_id = session.get("auth_session_id")
        g.user = get_user_by_id(app.config["SQLITE_PATH"], user_id) if user_id else None
        g.session_record = None

        if not app.config["USE_PERSISTENT_AUTH_SESSIONS"]:
            if g.user is None:
                if not _is_public_endpoint() and user_id:
                    return _handle_inactive_session("session_ended")
                return None

            if not auth_session_id:
                _ensure_stateless_auth_session(g.user["id"])
            g.session_record = _stateless_session_record(g.user["id"])
            return None

        if g.user and not auth_session_id:
            auth_session_id = str(uuid4())
            create_user_session(
                app.config["SQLITE_PATH"],
                session_id=auth_session_id,
                user_id=g.user["id"],
            )
            session["auth_session_id"] = auth_session_id
            g.session_record = get_user_session(app.config["SQLITE_PATH"], auth_session_id)
            return None

        if not user_id or not auth_session_id:
            return None

        max_idle_seconds = app.config["SESSION_IDLE_SECONDS"] + app.config["SESSION_WARNING_SECONDS"]
        session_record = expire_user_session_if_idle(
            app.config["SQLITE_PATH"],
            auth_session_id,
            max_idle_seconds=max_idle_seconds,
        )

        if (
            g.user is None
            or session_record is None
            or not session_record["is_active"]
            or session_record["user_id"] != user_id
        ):
            reason = session_record.get("end_reason") if session_record else "session_ended"
            if _is_public_endpoint():
                session.clear()
                if reason != "unauthenticated":
                    flash(_session_message_for_reason(reason), "info")
                return None
            return _handle_inactive_session(reason)

        g.session_record = session_record
        return None

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

            _start_authenticated_session(user["id"])
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

            _start_authenticated_session(user["id"])
            return redirect(url_for("chat_page"))

        return render_template("register.html", form_data=form_data)

    @app.post("/logout")
    @login_required
    def logout():
        auth_session_id = session.get("auth_session_id")
        if auth_session_id and current_app.config["USE_PERSISTENT_AUTH_SESSIONS"]:
            end_user_session(app.config["SQLITE_PATH"], auth_session_id, reason="logout")
        session.clear()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    @app.post("/session/end")
    @login_required
    def end_session():
        payload = request.get_json(silent=True) or {}
        reason = (payload.get("reason") or "manual_end").strip() or "manual_end"
        auth_session_id = session.get("auth_session_id")

        if auth_session_id and current_app.config["USE_PERSISTENT_AUTH_SESSIONS"]:
            end_user_session(app.config["SQLITE_PATH"], auth_session_id, reason=reason)

        session.clear()
        return jsonify(
            {
                "ok": True,
                "reason": reason,
                "redirectUrl": url_for("login"),
                "message": _session_message_for_reason(reason),
            }
        )

    @app.post("/session/activity")
    @login_required
    def session_activity():
        if not current_app.config["USE_PERSISTENT_AUTH_SESSIONS"]:
            if g.user is None:
                return _json_session_ended("session_ended")
            _touch_stateless_session()
            return jsonify({"ok": True, "session": _serialize_session_record(g.session_record)})

        auth_session_id = session.get("auth_session_id")
        if not auth_session_id:
            return _json_session_ended("session_ended")

        session_record = expire_user_session_if_idle(
            app.config["SQLITE_PATH"],
            auth_session_id,
            max_idle_seconds=app.config["SESSION_IDLE_SECONDS"] + app.config["SESSION_WARNING_SECONDS"],
        )
        if session_record is None or not session_record["is_active"]:
            return _json_session_ended((session_record or {}).get("end_reason", "session_ended"))

        updated = touch_user_session(app.config["SQLITE_PATH"], auth_session_id)
        if updated is None:
            return _json_session_ended("session_ended")

        g.session_record = updated
        return jsonify(
            {
                "ok": True,
                "session": _serialize_session_record(updated),
            }
        )

    @app.get("/session/status")
    @login_required
    def session_status():
        if not current_app.config["USE_PERSISTENT_AUTH_SESSIONS"]:
            if g.user is None:
                return _json_session_ended("session_ended")
            return jsonify(
                {
                    "ok": True,
                    "session": _serialize_session_record(g.session_record),
                    "idleTimeoutSeconds": app.config["SESSION_IDLE_SECONDS"],
                    "warningTimeoutSeconds": app.config["SESSION_WARNING_SECONDS"],
                }
            )

        auth_session_id = session.get("auth_session_id")
        if not auth_session_id:
            return _json_session_ended("session_ended")

        session_record = get_user_session(app.config["SQLITE_PATH"], auth_session_id)
        if session_record is None or not session_record["is_active"]:
            return _json_session_ended((session_record or {}).get("end_reason", "session_ended"))

        return jsonify(
            {
                "ok": True,
                "session": _serialize_session_record(session_record),
                "idleTimeoutSeconds": app.config["SESSION_IDLE_SECONDS"],
                "warningTimeoutSeconds": app.config["SESSION_WARNING_SECONDS"],
            }
        )

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
            auth_session=_serialize_session_record(g.session_record),
            session_config={
                "idleTimeoutSeconds": app.config["SESSION_IDLE_SECONDS"],
                "warningTimeoutSeconds": app.config["SESSION_WARNING_SECONDS"],
            },
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

        response_payload, status_code = _process_chat_message(session_id, message)
        return jsonify(_camelize_payload(response_payload)), status_code

    @app.post("/voice")
    @login_required
    def voice():
        session_id = request.form.get("sessionId", "").strip()
        audio_file = request.files.get("audio")

        if not session_id or audio_file is None:
            return jsonify({"error": "sessionId and audio are required."}), 400

        audio_bytes = audio_file.read()
        if not audio_bytes:
            return jsonify({"error": "Couldn't hear you, please try again."}), 400

        try:
            transcription = transcribe_audio(
                openai_client,
                audio_bytes=audio_bytes,
                filename=audio_file.filename or "recording.webm",
            )
        except Exception:
            return jsonify({"error": "Voice transcription is unavailable right now. Please try typing instead."}), 502

        transcribed_text = transcription["text"].strip()
        if not transcribed_text:
            return jsonify({"error": "Couldn't hear you, please try again."}), 400

        response_payload, status_code = _process_chat_message(session_id, transcribed_text)
        payload = _camelize_payload(response_payload)
        payload["transcribedText"] = transcribed_text
        return jsonify(payload), status_code


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if _wants_json_response():
                return _json_session_ended("unauthenticated", status_code=401)
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
        "normalized": payload.get("normalized"),
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


def _start_authenticated_session(user_id: str) -> Dict[str, object]:
    session.clear()
    auth_session_id = str(uuid4())
    session["user_id"] = user_id
    session["auth_session_id"] = auth_session_id
    session["auth_session_created_at"] = _utc_now_iso()
    session["auth_session_last_activity"] = session["auth_session_created_at"]
    if current_app.config["USE_PERSISTENT_AUTH_SESSIONS"]:
        create_user_session(current_app.config["SQLITE_PATH"], session_id=auth_session_id, user_id=user_id)
    return {"user_id": user_id, "auth_session_id": auth_session_id}


def _refresh_authenticated_session() -> None:
    auth_session_id = session.get("auth_session_id")
    if not auth_session_id:
        return
    if not current_app.config["USE_PERSISTENT_AUTH_SESSIONS"]:
        _touch_stateless_session()
        return
    updated = touch_user_session(current_app.config["SQLITE_PATH"], auth_session_id)
    if updated is not None:
        g.session_record = updated


def _ensure_stateless_auth_session(user_id: str) -> None:
    session["user_id"] = user_id
    session["auth_session_id"] = session.get("auth_session_id") or str(uuid4())
    session["auth_session_created_at"] = session.get("auth_session_created_at") or _utc_now_iso()
    session["auth_session_last_activity"] = session.get("auth_session_last_activity") or session["auth_session_created_at"]
    g.session_record = _stateless_session_record(user_id)


def _touch_stateless_session() -> None:
    if g.user is None:
        return
    session["auth_session_last_activity"] = _utc_now_iso()
    g.session_record = _stateless_session_record(g.user["id"])


def _stateless_session_record(user_id: str) -> Dict[str, object]:
    created_at = session.get("auth_session_created_at") or _utc_now_iso()
    last_activity = session.get("auth_session_last_activity") or created_at
    return {
        "session_id": session.get("auth_session_id"),
        "user_id": user_id,
        "is_active": True,
        "last_activity_timestamp": last_activity,
        "created_at": created_at,
        "ended_at": None,
        "end_reason": None,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handle_inactive_session(reason: str):
    session.clear()
    message = _session_message_for_reason(reason)
    if _wants_json_response():
        return _json_session_ended(reason)
    if message:
        flash(message, "info")
    return redirect(url_for("login"))


def _json_session_ended(reason: str, status_code: int = 440):
    return (
        jsonify(
            {
                "error": _session_message_for_reason(reason),
                "reason": reason,
                "sessionEnded": True,
                "redirectUrl": url_for("login"),
            }
        ),
        status_code,
    )


def _session_message_for_reason(reason: str) -> str:
    messages = {
        "logout": "You have been logged out.",
        "manual_end": "Your session has been ended.",
        "idle_timeout": "Session expired due to inactivity.",
        "unauthenticated": "Please sign in to continue.",
        "session_ended": "Your session is no longer active.",
    }
    return messages.get(reason, "Your session is no longer active.")


def _serialize_session_record(record: Dict[str, object] | None) -> Dict[str, object] | None:
    if record is None:
        return None
    return {
        "sessionId": record["session_id"],
        "userId": record["user_id"],
        "isActive": bool(record["is_active"]),
        "lastActivityTimestamp": record["last_activity_timestamp"],
        "createdAt": record["created_at"],
        "endedAt": record["ended_at"],
        "endReason": record.get("end_reason"),
    }


def _wants_json_response() -> bool:
    if request.endpoint in {
        "chat",
        "update_profile_route",
        "history",
        "sessions",
        "session_detail",
        "end_session",
        "session_activity",
        "session_status",
    }:
        return True
    return request.accept_mimetypes.best == "application/json"


def _is_public_endpoint() -> bool:
    return request.endpoint in {"root", "login", "register", "health", "static"}


def _process_chat_message(session_id: str, message: str):
    user = g.user
    _refresh_authenticated_session()
    session_key = f"{user['id']}:{session_id}"
    session_profile = _profile_payload(user)
    session_state = get_session(session_key, base_profile=session_profile)
    openai_client = current_app.extensions.get("openai_client")

    append_message(session_key, "user", message)
    _persist_message(user["id"], session_id, "user", message)
    maybe_refresh_session_summary(
        db_path=current_app.config["SQLITE_PATH"],
        user_id=user["id"],
        session_id=session_id,
        client=openai_client,
        model=current_app.config["OPENAI_MODEL"],
    )

    history_context = summarize_patient_history(
        user_id=user["id"],
        db_path=current_app.config["SQLITE_PATH"],
        client=openai_client,
        model=current_app.config["OPENAI_MODEL"],
        limit=50,
    )

    try:
        response_payload = handle_chat(
            user_input=message,
            session_key=session_key,
            session=session_state,
            openai_client=openai_client,
            model=current_app.config["OPENAI_MODEL"],
            patient_history_context=history_context,
            db_path=current_app.config["SQLITE_PATH"],
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
            db_path=current_app.config["SQLITE_PATH"],
            user_id=user["id"],
            session_id=session_id,
            client=openai_client,
            model=current_app.config["OPENAI_MODEL"],
            force=response_payload.get("stage") == "TRIAGE_RESULT",
        )
        return response_payload, 200
    except Exception:
        fallback_reply = sanitize_assistant_text(
            "I'm having trouble completing the triage assessment right now, so the safest next step is to "
            "contact a doctor. If symptoms are severe or worsening, seek urgent care immediately."
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
            db_path=current_app.config["SQLITE_PATH"],
            user_id=user["id"],
            session_id=session_id,
            client=openai_client,
            model=current_app.config["OPENAI_MODEL"],
            force=True,
        )
        return (
            {
                "reply": fallback_reply,
                "assessment": sanitize_assessment(fallback_assessment),
                "stage": "TRIAGE_RESULT",
                "progressLabel": "Final step: Triage result",
                "normalized": {
                    "symptoms": session_state["triage"].get("symptoms", []),
                    "duration_days": session_state["triage"].get("duration_days"),
                    "severity": session_state["triage"].get("severity"),
                },
                "debug": {
                    **session_state["triage"],
                    "history_summary": history_context.get("generated_summary"),
                    "raw_history": history_context.get("raw_history"),
                },
            },
            500,
        )
