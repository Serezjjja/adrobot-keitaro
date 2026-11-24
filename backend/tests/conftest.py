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
def database_url(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    return url


@pytest.fixture
def fake():
    return FakeKeitaro()


@pytest.fixture
def client(database_url, fake):
    settings = Settings(
        database_url=database_url,
        keitaro_api_key="fake-key",
        campaign_prefix="test_demo_",
    )
    application = create_app(settings, httpx.MockTransport(fake))
    with TestClient(application) as api:
        yield api
