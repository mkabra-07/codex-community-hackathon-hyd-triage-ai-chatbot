import os
import tempfile
import unittest

from triage_app.chat_flow import handle_chat
from triage_app.context_engine import build_context
from triage_app.database import initialize_database
from triage_app.session_store import _SESSIONS, get_session


class ContextEngineTests(unittest.TestCase):
    def setUp(self):
        _SESSIONS.clear()

    def test_build_context_combines_profile_history_and_current_message(self):
        context = build_context(
            {"age": 45, "existing_conditions": "asthma, diabetes"},
            "Prior episodes of cough were discussed.",
            "Cough for 3 days with mild fever.",
        )

        self.assertEqual(context["profile"]["age"], 45)
        self.assertEqual(context["profile"]["conditions"], "asthma, diabetes")
        self.assertEqual(context["history_summary"], "Prior episodes of cough were discussed.")
        self.assertEqual(context["current_message"], "Cough for 3 days with mild fever.")

    def test_returning_user_skips_known_profile_questions(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            initialize_database(db_path)
            session = get_session(
                "returning-user:test",
                base_profile={"age": 31, "existing_conditions": "asthma"},
            )

            handle_chat("I have a fever", "returning-user:test", session, None, "test-model", db_path=db_path, user_id="returning-user")
            handle_chat("2 days", "returning-user:test", session, None, "test-model", db_path=db_path, user_id="returning-user")
            response = handle_chat(
                "moderate",
                "returning-user:test",
                session,
                None,
                "test-model",
                db_path=db_path,
                user_id="returning-user",
            )
        finally:
            pass

        self.assertEqual(response["stage"], "FOLLOW_UPS")
        self.assertIn("highest temperature", response["reply"])
        self.assertNotIn("age", response["reply"].lower())
        self.assertNotIn("chronic conditions", response["reply"].lower())
        self.assertNotIn("profile_age", response["debug"]["pending_follow_ups"])
        self.assertNotIn("profile_existing_conditions", response["debug"]["pending_follow_ups"])

    def test_new_user_gets_full_intake_for_missing_profile(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            initialize_database(db_path)
            session = get_session("new-user:test", base_profile={"age": "", "existing_conditions": ""})

            handle_chat("I have a cough", "new-user:test", session, None, "test-model", db_path=db_path, user_id="new-user")
            handle_chat("3 days", "new-user:test", session, None, "test-model", db_path=db_path, user_id="new-user")
            age_question = handle_chat(
                "mild",
                "new-user:test",
                session,
                None,
                "test-model",
                db_path=db_path,
                user_id="new-user",
            )
        finally:
            pass
            conditions_question = handle_chat(
                "29",
                "new-user:test",
                session,
                None,
                "test-model",
                db_path=db_path,
                user_id="new-user",
            )
            final_response = handle_chat(
                "none",
                "new-user:test",
                session,
                None,
                "test-model",
                db_path=db_path,
                user_id="new-user",
            )

        self.assertIn("patient's age", age_question["reply"].lower())
        self.assertIn("chronic conditions", conditions_question["reply"].lower())
        self.assertEqual(session["profile"]["age"], 29)
        self.assertEqual(session["profile"]["existing_conditions"], "none")
        self.assertEqual(final_response["stage"], "TRIAGE_RESULT")
        self.assertIsNotNone(final_response["assessment"])


if __name__ == "__main__":
    unittest.main()
