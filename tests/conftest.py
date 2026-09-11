from datetime import date

import pytest

from app import create_app, db
from app import services


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "today", lambda: date(2026, 9, 11))  # a Friday
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "IDENTITY_HEADER": "X-Forwarded-Email",
        "MOCK_AUTH_EMAIL": "",
        "USERS": [
            {"email": "lenya@example.com", "name": "Лёня", "side": "left"},
            {"email": "anya@example.com", "name": "Аня", "side": "right"},
        ],
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def as_lenya(client):
    client.environ_base["HTTP_X_FORWARDED_EMAIL"] = "lenya@example.com"
    return client


@pytest.fixture()
def demo(app):
    """Seed library + demo recipes, inside an app context."""
    with app.app_context():
        services.seed_demo_recipes()
        yield
