import json
import os
import sqlite3


def initialize_database(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()


def persist_message(db_path: str, session_id: str, role: str, message: str, metadata=None) -> None:
    if not db_path:
        return

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO conversations (session_id, role, message, metadata) VALUES (?, ?, ?, ?)",
            (session_id, role, message, json.dumps(metadata) if metadata else None),
        )
        connection.commit()
