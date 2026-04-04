import tempfile
import unittest

from triage_app.chat_flow import build_initial_prompt, handle_chat
from triage_app.database import initialize_database
from triage_app.message_format import GLOBAL_DISCLAIMER, sanitize_assistant_text
from triage_app.session_store import _SESSIONS, get_session


class MessageFormatTests(unittest.TestCase):
    def setUp(self):
        _SESSIONS.clear()

    def test_sanitize_assistant_text_removes_global_disclaimer(self):
        text = "This is not medical advice. Please share more details."
        self.assertEqual(sanitize_assistant_text(text), "Please share more details.")

    def test_initial_prompt_uses_clean_message_text(self):
        session = get_session("initial-prompt:test", base_profile={"age": 31, "existing_conditions": "asthma"})
        prompt = build_initial_prompt(session)
        self.assertNotIn(GLOBAL_DISCLAIMER, prompt["reply"])

    def test_chat_responses_do_not_repeat_disclaimer(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            initialize_database(db_file.name)
            session = get_session("clean-reply:test", base_profile={"age": 31, "existing_conditions": "asthma"})

            first_response = handle_chat(
                "I have a fever",
                "clean-reply:test",
                session,
                None,
                "test-model",
                db_path=db_file.name,
                user_id="clean-reply",
            )
            second_response = handle_chat(
                "2 days",
                "clean-reply:test",
                session,
                None,
                "test-model",
                db_path=db_file.name,
                user_id="clean-reply",
            )

        self.assertNotIn(GLOBAL_DISCLAIMER, first_response["reply"])
        self.assertNotIn(GLOBAL_DISCLAIMER, second_response["reply"])


if __name__ == "__main__":
    unittest.main()
