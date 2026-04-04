# AI Healthcare Triage Chatbot MVP

This repo is now a Python-first Flask implementation of the healthcare triage chatbot MVP. It serves the chat UI, handles the triage API, applies emergency safety rules, and optionally stores chat history in SQLite.

## What it includes

- Flask app that serves both the frontend and backend
- WhatsApp-style chat interface rendered from a Flask template
- OpenAI-powered structured JSON triage assessment
- Hybrid safety logic with hardcoded emergency red flags
- In-memory conversation state by session ID
- Optional SQLite persistence for user and assistant messages

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
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Environment variables

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4.1-mini
SECRET_KEY=replace_me
PORT=5000
ENABLE_SQLITE=false
SQLITE_PATH=data/triage.db
```

## Run locally

```bash
python app.py
```

App URL: `http://127.0.0.1:5000`

Health check: `http://127.0.0.1:5000/health`

## API

### `POST /chat`

Request body:

```json
{
  "sessionId": "abc123",
  "message": "I have had a fever and cough for two days."
}
```

Response shape:

```json
{
  "reply": "This is not medical advice. ...",
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
  - age
  - existing conditions
- The system never diagnoses and always includes the disclaimer: `This is not medical advice.`
- If the model is unavailable or uncertain, the workflow escalates conservatively to a clinician.

## Notes

- Active conversation state is stored in memory.
- If `ENABLE_SQLITE=true`, messages are also stored in SQLite.
- This app is for triage UX and urgency guidance, not diagnosis or treatment.
