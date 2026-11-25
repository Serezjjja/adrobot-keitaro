from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.main import create_app
from tests.fakes import FakeKeitaro


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # Alembic and both app instances must see the same isolated test configuration.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("KEITARO_BASE_URL", "https://tracker.example")
    monkeypatch.setenv("KEITARO_API_KEY", "fake-key")
    monkeypatch.setenv("CAMPAIGN_PREFIX", "test_demo_")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    return Settings()


@pytest.fixture
def fake():
    return FakeKeitaro()


@pytest.fixture
def client(settings, fake):
    application = create_app(settings, httpx.MockTransport(fake))
    with TestClient(application) as api:
        yield api
