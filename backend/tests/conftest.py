import os
import tempfile

# Point the app at a throwaway data dir before any app module reads settings.
# DATABASE_URL is pinned too, so a value from the shell or .env can't select a
# real database that teardown would then drop.
TEST_DATA_DIR = tempfile.mkdtemp(prefix="ara-test-")
TEST_DB_PATH = os.path.join(TEST_DATA_DIR, "app.db")
os.environ["DATA_DIR"] = TEST_DATA_DIR
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

assert engine.url.database == TEST_DB_PATH, f"tests must not use {engine.url}"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)
