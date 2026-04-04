import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    PORT = int(os.getenv("PORT", "5000"))
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    ENABLE_SQLITE = True
    SQLITE_PATH = os.getenv("SQLITE_PATH", "data/triage.db")
    USE_PERSISTENT_AUTH_SESSIONS = os.getenv("VERCEL") != "1"
    SESSION_IDLE_SECONDS = int(os.getenv("SESSION_IDLE_SECONDS", "60"))
    SESSION_WARNING_SECONDS = int(os.getenv("SESSION_WARNING_SECONDS", "30"))
