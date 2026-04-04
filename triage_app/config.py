import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    PORT = int(os.getenv("PORT", "5000"))
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    ENABLE_SQLITE = os.getenv("ENABLE_SQLITE", "false").lower() == "true"
    SQLITE_PATH = os.getenv("SQLITE_PATH", "data/triage.db")
