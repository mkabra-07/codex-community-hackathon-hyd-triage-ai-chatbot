# AI Healthcare Triage Chatbot MVP

This repo is now a Python-first Flask implementation of the healthcare triage chatbot MVP. It serves the chat UI, handles the triage API, applies emergency safety rules, and stores users, patient profiles, and chat history in SQLite.

Demo video: [Watch the walkthrough](https://drive.google.com/file/d/1gA6yqnrQ-43cCQRzsVBTKGGGt8B_LVls/view?usp=share_link)

## What it includes

- Flask app that serves both the frontend and backend
- WhatsApp-style chat interface rendered from a Flask template
- Simple login and registration with SQLite-backed user accounts
- Persistent patient profiles reused across sessions
- OpenAI-powered structured JSON triage assessment
- Hybrid safety logic with hardcoded emergency red flags
- In-memory conversation state by session ID
- SQLite persistence for users and conversations

## Folder structure

```text
AITriageChatbot/
  app.py
  requirements.txt
  triage_app/
    __init__.py
    config.py
    database.py
    openai_service.py
    routes.py
    session_store.py
    triage_engine.py
  templates/
    index.html
  static/
    styles.css
  .env.example
  README.md
```

## Prerequisites

- Python 3.10+
- An OpenAI API key

## Setup

1. Create a virtual environment.
2. Install dependencies.
3. Copy `.env.example` to `.env`.
4. Fill in `OPENAI_API_KEY`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment variables

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4.1-mini
SECRET_KEY=replace_me
PORT=5000
SQLITE_PATH=data/triage.db
```

## Run locally

```bash
python3 app.py
```

App URL: `http://127.0.0.1:5000`

Health check: `http://127.0.0.1:5000/health`

## Authentication

- Login page: `http://127.0.0.1:5000/login`
- Register page: `http://127.0.0.1:5000/register`
- After login, users are redirected to the chatbot.
- Flask session cookies keep the user signed in.

## How To Navigate The App

1. Open `http://127.0.0.1:5000/login` and sign in with a seeded account or create a new one.
2. After login, land on the main chat screen with the global safety banner at the top.
3. Review the patient profile card before chatting. If details are missing, save the profile first so the bot can avoid re-asking the same questions.
4. Type symptoms into the composer at the bottom, or use the microphone button for voice input when supported.
5. Follow the staged chat flow: symptoms, duration, severity, then follow-up questions when needed.
6. Use the left sidebar to reopen previous chats and review stored session summaries.
7. Use `End Session` to close the current triage session, or `Logout` to leave the app entirely.

### Seeded sample users

- `aarav.sharma@careflow.app / aarav123`
- `sara.khan@careflow.app / sara123`
- `neha.patel@careflow.app / neha123`
- `rohan.mehta@careflow.app / rohan123`

Some sample users have missing fields so you can test the first-login profile completion flow.

## API

### `POST /chat`

Request body:

```json
{
  "sessionId": "abc123",
  "message": "I have had a fever and cough for two days."
}
```

### `POST /profile`

Saves persistent patient profile fields used across future sessions.

```json
{
  "age": 29,
  "gender": "female",
  "height": 165,
  "weight": 60,
  "existing_conditions": "asthma, migraine"
}
```

Response shape:

```json
{
  "reply": "Please share the symptoms you are experiencing.",
  "assessment": {
    "symptoms": ["fever", "cough"],
    "risk_level": "URGENT",
    "reasoning": "Reasoning text",
    "next_steps": ["Consult a doctor within 24 hours"]
  },
  "followUpQuestions": [
    "How long have these symptoms been going on?"
  ]
}
```

## Triage behavior

- Emergency red flags always escalate immediately:
  - chest pain
  - difficulty breathing
  - unconsciousness
  - severe bleeding
- The bot asks follow-up questions for:
  - duration
  - severity
- It uses saved patient profile details automatically:
  - age
  - gender
  - height
  - weight
  - existing conditions
- It does not re-ask stored profile details unless they are missing.
- The global warning banner stays visible at the top of the chat UI for safety messaging.
- Individual assistant messages stay clean and do not repeat the warning banner copy.
- If the model is unavailable or uncertain, the workflow escalates conservatively to a clinician.

## Notes

- User accounts and conversations are stored in SQLite.
- Active chat turn state is stored in memory per authenticated user session key.
- This app is for triage UX and urgency guidance, not diagnosis or treatment.
