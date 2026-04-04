import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash


SAMPLE_USERS = [
    {
        "name": "Aarav Sharma",
        "email": "aarav.sharma@careflow.app",
        "password": "aarav123",
        "age": 31,
        "gender": "male",
        "height": 176,
        "weight": 74,
        "existing_conditions": ["asthma"],
    },
    {
        "name": "Sara Khan",
        "email": "sara.khan@careflow.app",
        "password": "sara123",
        "age": 27,
        "gender": "female",
        "height": 164,
        "weight": 58,
        "existing_conditions": [],
    },
    {
        "name": "Neha Patel",
        "email": "neha.patel@careflow.app",
        "password": "neha123",
        "age": None,
        "gender": "female",
        "height": None,
        "weight": None,
        "existing_conditions": None,
    },
    {
        "name": "Rohan Mehta",
        "email": "rohan.mehta@careflow.app",
        "password": "rohan123",
        "age": 45,
        "gender": "male",
        "height": 172,
        "weight": 83,
        "existing_conditions": ["hypertension", "type 2 diabetes"],
    },
]


def _connect(db_path: str):
    return closing(sqlite3.connect(db_path))


def _database_is_healthy(db_path: str) -> bool:
    if not os.path.exists(db_path):
        return False

    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute("PRAGMA integrity_check;").fetchone()
    except sqlite3.DatabaseError:
        return False

    return bool(row) and row[0] == "ok"


def prepare_database_path(db_path: str) -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    source_path = db_path if os.path.isabs(db_path) else os.path.join(project_root, db_path)

    if os.getenv("VERCEL") != "1":
        return source_path

    runtime_dir = os.path.join("/tmp", "triage-healthcare-chatbot")
    os.makedirs(runtime_dir, exist_ok=True)
    runtime_path = os.path.join(runtime_dir, os.path.basename(source_path))

    if not os.path.exists(runtime_path) and _database_is_healthy(source_path):
        shutil.copy2(source_path, runtime_path)

    return runtime_path


def initialize_database(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                age INTEGER,
                gender TEXT,
                height REAL,
                weight REAL,
                existing_conditions TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history_summaries (
                user_id TEXT PRIMARY KEY,
                last_message_id INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_summaries (
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                message_count INTEGER NOT NULL DEFAULT 0,
                summary TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, session_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_activity_timestamp DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                ended_at DATETIME,
                end_reason TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversations_user_session_id
            ON conversations (user_id, session_id, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id
            ON user_sessions (user_id, is_active)
            """
        )
        _ensure_user_email_column(connection)
        connection.commit()

    seed_users(db_path)
    backfill_user_emails(db_path)


def seed_users(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        existing_count = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]

        if existing_count:
            return

        for user in SAMPLE_USERS:
            connection.execute(
                """
                INSERT INTO users (id, name, email, password_hash, age, gender, height, weight, existing_conditions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _slugify(user["name"]),
                    user["name"],
                    user["email"],
                    generate_password_hash(user["password"]),
                    user["age"],
                    user["gender"],
                    user["height"],
                    user["weight"],
                    json.dumps(user["existing_conditions"]) if user["existing_conditions"] is not None else None,
                ),
            )
        connection.commit()


def create_user(
    db_path: str,
    *,
    name: str,
    email: str,
    password: str,
    gender: str = "",
    age: Optional[int] = None,
    height: Optional[float] = None,
    weight: Optional[float] = None,
    existing_conditions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    user_id = _unique_user_id(db_path, name)
    with _connect(db_path) as connection:
        try:
            connection.execute(
                """
                INSERT INTO users (id, name, email, password_hash, age, gender, height, weight, existing_conditions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    name.strip(),
                    email.strip().lower(),
                    generate_password_hash(password),
                    age,
                    gender.strip() or None,
                    height,
                    weight,
                    json.dumps(existing_conditions) if existing_conditions is not None else None,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            raise ValueError("A user with that name or email already exists.") from error

    return get_user_by_id(db_path, user_id)


def authenticate_user(db_path: str, identifier: str, password: str) -> Optional[Dict[str, Any]]:
    user = get_user_by_email(db_path, identifier) or get_user_by_name(db_path, identifier)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def get_user_by_id(db_path: str, user_id: str) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row)


def get_user_by_name(db_path: str, name: str) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM users WHERE lower(name) = lower(?)", (name.strip(),)).fetchone()
        return _row_to_user(row)


def get_user_by_email(db_path: str, email: str) -> Optional[Dict[str, Any]]:
    normalized_email = email.strip().lower()
    if not normalized_email:
        return None
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (normalized_email,)).fetchone()
        return _row_to_user(row)


def update_user_profile(
    db_path: str,
    user_id: str,
    *,
    age: Optional[int] = None,
    gender: Optional[str] = None,
    height: Optional[float] = None,
    weight: Optional[float] = None,
    existing_conditions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    user = get_user_by_id(db_path, user_id)
    if not user:
        raise ValueError("User not found.")

    next_user = {
        "age": age if age is not None else user["age"],
        "gender": gender if gender is not None and gender != "" else user["gender"],
        "height": height if height is not None else user["height"],
        "weight": weight if weight is not None else user["weight"],
        "existing_conditions": existing_conditions if existing_conditions is not None else user["existing_conditions"],
    }

    with _connect(db_path) as connection:
        connection.execute(
            """
            UPDATE users
            SET age = ?, gender = ?, height = ?, weight = ?, existing_conditions = ?
            WHERE id = ?
            """,
            (
                next_user["age"],
                next_user["gender"],
                next_user["height"],
                next_user["weight"],
                json.dumps(next_user["existing_conditions"]) if next_user["existing_conditions"] is not None else None,
                user_id,
            ),
        )
        connection.commit()

    return get_user_by_id(db_path, user_id)


def persist_message(
    db_path: str,
    user_id: str,
    session_id: str,
    role: str,
    message: str,
    metadata=None,
) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO conversations (user_id, session_id, role, message, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, session_id, role, message, json.dumps(metadata) if metadata else None),
        )
        connection.commit()


def create_user_session(db_path: str, *, session_id: str, user_id: str) -> Dict[str, Any]:
    now = _utc_now_sql()
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO user_sessions (
                session_id,
                user_id,
                is_active,
                last_activity_timestamp,
                created_at,
                ended_at,
                end_reason
            )
            VALUES (?, ?, 1, ?, ?, NULL, NULL)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id = excluded.user_id,
                is_active = 1,
                last_activity_timestamp = excluded.last_activity_timestamp,
                created_at = excluded.created_at,
                ended_at = NULL,
                end_reason = NULL
            """,
            (session_id, user_id, now, now),
        )
        connection.commit()
    return get_user_session(db_path, session_id)


def get_user_session(db_path: str, session_id: str) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                session_id,
                user_id,
                is_active,
                last_activity_timestamp,
                created_at,
                ended_at,
                end_reason
            FROM user_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    payload = dict(row)
    payload["is_active"] = bool(payload["is_active"])
    return payload


def touch_user_session(db_path: str, session_id: str) -> Optional[Dict[str, Any]]:
    now = _utc_now_sql()
    with _connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE user_sessions
            SET last_activity_timestamp = ?
            WHERE session_id = ? AND is_active = 1
            """,
            (now, session_id),
        )
        connection.commit()
    if cursor.rowcount == 0:
        return None
    return get_user_session(db_path, session_id)


def end_user_session(db_path: str, session_id: str, *, reason: str = "manual") -> Optional[Dict[str, Any]]:
    now = _utc_now_sql()
    with _connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE user_sessions
            SET
                is_active = 0,
                ended_at = COALESCE(ended_at, ?),
                end_reason = COALESCE(end_reason, ?)
            WHERE session_id = ?
            """,
            (now, reason, session_id),
        )
        connection.commit()
    if cursor.rowcount == 0:
        return None
    return get_user_session(db_path, session_id)


def expire_user_session_if_idle(
    db_path: str,
    session_id: str,
    *,
    max_idle_seconds: int,
    reason: str = "idle_timeout",
) -> Optional[Dict[str, Any]]:
    record = get_user_session(db_path, session_id)
    if record is None or not record["is_active"]:
        return record

    last_activity = _parse_sqlite_timestamp(record["last_activity_timestamp"])
    if last_activity is None:
        return end_user_session(db_path, session_id, reason=reason)

    idle_for = datetime.now(timezone.utc) - last_activity
    if idle_for >= timedelta(seconds=max_idle_seconds):
        return end_user_session(db_path, session_id, reason=reason)
    return record


def fetch_recent_conversations(db_path: str, user_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, session_id, role, message, metadata, created_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

    conversations = [dict(row) for row in reversed(rows)]
    for item in conversations:
        item["metadata"] = json.loads(item["metadata"]) if item.get("metadata") else None
    return conversations


def fetch_session_messages(db_path: str, user_id: str, session_id: str) -> List[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, session_id, role, message, metadata, created_at
            FROM conversations
            WHERE user_id = ? AND session_id = ?
            ORDER BY id ASC
            """,
            (user_id, session_id),
        ).fetchall()

    messages = [dict(row) for row in rows]
    for item in messages:
        item["metadata"] = json.loads(item["metadata"]) if item.get("metadata") else None
    return messages


def fetch_session_overview(
    db_path: str,
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            WITH session_stats AS (
                SELECT
                    session_id,
                    MIN(created_at) AS created_at,
                    MAX(created_at) AS updated_at,
                    COUNT(*) AS message_count,
                    MAX(id) AS last_message_id
                FROM conversations
                WHERE user_id = ?
                GROUP BY session_id
            )
            SELECT
                session_stats.session_id,
                session_stats.created_at,
                session_stats.updated_at,
                session_stats.message_count,
                session_stats.last_message_id,
                session_summaries.summary AS cached_summary,
                session_summaries.last_message_id AS summary_last_message_id
            FROM session_stats
            LEFT JOIN session_summaries
                ON session_summaries.user_id = ?
                AND session_summaries.session_id = session_stats.session_id
            ORDER BY session_stats.last_message_id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, user_id, limit, offset),
        ).fetchall()

    return [dict(row) for row in rows]


def fetch_session_record(db_path: str, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            WITH session_stats AS (
                SELECT
                    session_id,
                    MIN(created_at) AS created_at,
                    MAX(created_at) AS updated_at,
                    COUNT(*) AS message_count,
                    MAX(id) AS last_message_id
                FROM conversations
                WHERE user_id = ? AND session_id = ?
                GROUP BY session_id
            )
            SELECT
                session_stats.session_id,
                session_stats.created_at,
                session_stats.updated_at,
                session_stats.message_count,
                session_stats.last_message_id,
                session_summaries.summary AS cached_summary,
                session_summaries.last_message_id AS summary_last_message_id
            FROM session_stats
            LEFT JOIN session_summaries
                ON session_summaries.user_id = ?
                AND session_summaries.session_id = session_stats.session_id
            """,
            (user_id, session_id, user_id),
        ).fetchone()

    return dict(row) if row else None


def count_sessions(db_path: str, user_id: str) -> int:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT COUNT(*) AS session_count
            FROM (
                SELECT session_id
                FROM conversations
                WHERE user_id = ?
                GROUP BY session_id
            )
            """,
            (user_id,),
        ).fetchone()
    return int(row["session_count"] or 0)


def get_latest_conversation_id(db_path: str, user_id: str) -> int:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT MAX(id) AS latest_id FROM conversations WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["latest_id"] or 0)


def get_cached_history_summary(db_path: str, user_id: str) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT user_id, last_message_id, summary_json, updated_at FROM history_summaries WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "user_id": row["user_id"],
        "last_message_id": row["last_message_id"],
        "summary": json.loads(row["summary_json"]),
        "updated_at": row["updated_at"],
    }


def get_cached_session_summary(db_path: str, user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT user_id, session_id, created_at, last_message_id, message_count, summary, updated_at
            FROM session_summaries
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def upsert_history_summary(db_path: str, user_id: str, last_message_id: int, summary: Dict[str, Any]) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO history_summaries (user_id, last_message_id, summary_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                last_message_id = excluded.last_message_id,
                summary_json = excluded.summary_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, last_message_id, json.dumps(summary)),
        )
        connection.commit()


def upsert_session_summary(
    db_path: str,
    *,
    user_id: str,
    session_id: str,
    created_at: str,
    last_message_id: int,
    message_count: int,
    summary: str,
) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO session_summaries (
                user_id,
                session_id,
                created_at,
                last_message_id,
                message_count,
                summary,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                created_at = excluded.created_at,
                last_message_id = excluded.last_message_id,
                message_count = excluded.message_count,
                summary = excluded.summary,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, session_id, created_at, last_message_id, message_count, summary),
        )
        connection.commit()


def missing_profile_fields(user: Dict[str, Any]) -> List[str]:
    missing = []
    if user.get("age") is None:
        missing.append("age")
    if user.get("height") is None:
        missing.append("height")
    if user.get("weight") is None:
        missing.append("weight")
    if user.get("existing_conditions") is None:
        missing.append("existing_conditions")
    return missing


def _row_to_user(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    raw_conditions = data.get("existing_conditions")
    data["existing_conditions"] = json.loads(raw_conditions) if raw_conditions is not None else None
    return data


def _ensure_user_email_column(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    if "email" not in columns:
        connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")


def backfill_user_emails(db_path: str) -> None:
    with _connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id, name, email FROM users").fetchall()
        for row in rows:
            if row["email"]:
                continue
            email = _default_email_for_name(row["name"])
            connection.execute(
                "UPDATE users SET email = ? WHERE id = ?",
                (email, row["id"]),
            )
        connection.commit()


def _slugify(name: str) -> str:
    return "-".join(name.lower().split())


def _unique_user_id(db_path: str, name: str) -> str:
    base = _slugify(name.strip()) or "user"
    candidate = base
    counter = 1
    while get_user_by_id(db_path, candidate):
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


def _default_email_for_name(name: str) -> str:
    local_part = ".".join(name.lower().split()) or "user"
    return f"{local_part}@careflow.app"


def _utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_sqlite_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    if "T" in normalized:
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
