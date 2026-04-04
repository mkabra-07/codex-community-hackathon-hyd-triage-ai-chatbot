import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash


SAMPLE_USERS = [
    {
        "name": "Aarav Sharma",
        "password": "aarav123",
        "age": 31,
        "gender": "male",
        "height": 176,
        "weight": 74,
        "existing_conditions": ["asthma"],
    },
    {
        "name": "Sara Khan",
        "password": "sara123",
        "age": 27,
        "gender": "female",
        "height": 164,
        "weight": 58,
        "existing_conditions": [],
    },
    {
        "name": "Neha Patel",
        "password": "neha123",
        "age": None,
        "gender": "female",
        "height": None,
        "weight": None,
        "existing_conditions": None,
    },
    {
        "name": "Rohan Mehta",
        "password": "rohan123",
        "age": 45,
        "gender": "male",
        "height": 172,
        "weight": 83,
        "existing_conditions": ["hypertension", "type 2 diabetes"],
    },
]


def initialize_database(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
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
        connection.commit()

    seed_users(db_path)


def seed_users(db_path: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        existing_count = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]

        if existing_count:
            return

        for user in SAMPLE_USERS:
            connection.execute(
                """
                INSERT INTO users (id, name, password_hash, age, gender, height, weight, existing_conditions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _slugify(user["name"]),
                    user["name"],
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
    password: str,
    gender: str = "",
    age: Optional[int] = None,
    height: Optional[float] = None,
    weight: Optional[float] = None,
    existing_conditions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    user_id = _unique_user_id(db_path, name)
    with sqlite3.connect(db_path) as connection:
        try:
            connection.execute(
                """
                INSERT INTO users (id, name, password_hash, age, gender, height, weight, existing_conditions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    name.strip(),
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
            raise ValueError("A user with that name already exists.") from error

    return get_user_by_id(db_path, user_id)


def authenticate_user(db_path: str, name: str, password: str) -> Optional[Dict[str, Any]]:
    user = get_user_by_name(db_path, name)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user


def get_user_by_id(db_path: str, user_id: str) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row)


def get_user_by_name(db_path: str, name: str) -> Optional[Dict[str, Any]]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM users WHERE lower(name) = lower(?)", (name.strip(),)).fetchone()
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

    with sqlite3.connect(db_path) as connection:
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
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO conversations (user_id, session_id, role, message, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, session_id, role, message, json.dumps(metadata) if metadata else None),
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
