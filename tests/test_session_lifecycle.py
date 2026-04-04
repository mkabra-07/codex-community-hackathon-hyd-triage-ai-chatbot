import os
import sqlite3
import tempfile
import unittest

from triage_app import create_app
from triage_app.config import Config
from triage_app.database import end_user_session, get_user_session


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.original_sqlite_path = Config.SQLITE_PATH
        self.original_idle_seconds = Config.SESSION_IDLE_SECONDS
        self.original_warning_seconds = Config.SESSION_WARNING_SECONDS

        Config.SQLITE_PATH = self.db_path
        Config.SESSION_IDLE_SECONDS = 60
        Config.SESSION_WARNING_SECONDS = 30

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        Config.SQLITE_PATH = self.original_sqlite_path
        Config.SESSION_IDLE_SECONDS = self.original_idle_seconds
        Config.SESSION_WARNING_SECONDS = self.original_warning_seconds

        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_login_creates_persistent_session_and_end_session_clears_cookie_state(self):
        response = self.client.post(
            "/login",
            data={"email": "aarav.sharma@careflow.app", "password": "aarav123"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)

        with self.client.session_transaction() as flask_session:
            auth_session_id = flask_session.get("auth_session_id")
            self.assertEqual(flask_session.get("user_id"), "aarav-sharma")
            self.assertIsNotNone(auth_session_id)

        session_record = get_user_session(self.db_path, auth_session_id)
        self.assertIsNotNone(session_record)
        self.assertTrue(session_record["is_active"])
        self.assertIsNone(session_record["ended_at"])

        activity_response = self.client.post("/session/activity")
        self.assertEqual(activity_response.status_code, 200)
        self.assertTrue(activity_response.get_json()["session"]["isActive"])

        end_response = self.client.post("/session/end", json={"reason": "manual_end"})
        self.assertEqual(end_response.status_code, 200)
        self.assertEqual(end_response.get_json()["reason"], "manual_end")

        with self.client.session_transaction() as flask_session:
            self.assertIsNone(flask_session.get("user_id"))
            self.assertIsNone(flask_session.get("auth_session_id"))

        ended_record = get_user_session(self.db_path, auth_session_id)
        self.assertFalse(ended_record["is_active"])
        self.assertEqual(ended_record["end_reason"], "manual_end")
        self.assertIsNotNone(ended_record["ended_at"])

    def test_inactive_session_is_rejected_for_api_requests(self):
        self.client.post(
            "/login",
            data={"email": "aarav.sharma@careflow.app", "password": "aarav123"},
            follow_redirects=False,
        )

        with self.client.session_transaction() as flask_session:
            auth_session_id = flask_session["auth_session_id"]

        end_user_session(self.db_path, auth_session_id, reason="manual_end")

        response = self.client.get("/sessions")
        self.assertEqual(response.status_code, 440)
        payload = response.get_json()
        self.assertTrue(payload["sessionEnded"])
        self.assertEqual(payload["reason"], "manual_end")

    def test_server_side_idle_expiration_marks_session_inactive(self):
        self.client.post(
            "/login",
            data={"email": "aarav.sharma@careflow.app", "password": "aarav123"},
            follow_redirects=False,
        )

        with self.client.session_transaction() as flask_session:
            auth_session_id = flask_session["auth_session_id"]

        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                UPDATE user_sessions
                SET last_activity_timestamp = datetime('now', '-2 minutes')
                WHERE session_id = ?
                """,
                (auth_session_id,),
            )
            connection.commit()

        response = self.client.post("/session/activity")
        self.assertEqual(response.status_code, 440)
        self.assertEqual(response.get_json()["reason"], "idle_timeout")

        session_record = get_user_session(self.db_path, auth_session_id)
        self.assertFalse(session_record["is_active"])
        self.assertEqual(session_record["end_reason"], "idle_timeout")


if __name__ == "__main__":
    unittest.main()
