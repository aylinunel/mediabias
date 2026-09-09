import pytest
from fastapi.testclient import TestClient

from mediabias.api import create_app
from mediabias.config import Settings
from mediabias.db import Base

TOKENS = {
    "r" * 24: {"id": "reviewer", "role": "reviewer"},
    "e" * 24: {"id": "editor", "role": "editor"},
    "f" * 24: {"id": "second-editor", "role": "editor"},
}


@pytest.fixture
def client():
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def secured():
    app = create_app(Settings(mode="live", database_url="sqlite:///:memory:", users=TOKENS))
    with app.state.sessions() as session:
        Base.metadata.create_all(session.get_bind())
    with TestClient(app) as client:
        yield client


def auth(char):
    return {"Authorization": "Bearer " + char * 24}
