from flask import Flask

from .config import Config
from .database import initialize_database
from .routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(Config)

    initialize_database(app.config["SQLITE_PATH"])

    register_routes(app)
    return app
