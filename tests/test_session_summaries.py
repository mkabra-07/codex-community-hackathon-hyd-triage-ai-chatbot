import unittest

from triage_app.session_summaries import generate_session_summary


class SessionSummaryTests(unittest.TestCase):
    def test_falls_back_to_trimmed_first_user_message(self):
        messages = [
            {"role": "user", "message": "I want help designing an AI triage chatbot MVP with better prompts and history handling for returning patients."},
            {"role": "assistant", "message": "Sure, let's break that down."},
        ]

        summary = generate_session_summary(messages, client=None, model="test-model")

        self.assertEqual(summary, "I want help designing an AI triage chatbot MVP with bet...")

    def test_empty_messages_use_new_chat_label(self):
        summary = generate_session_summary([], client=None, model="test-model")
        self.assertEqual(summary, "New Chat")


if __name__ == "__main__":
    unittest.main()
