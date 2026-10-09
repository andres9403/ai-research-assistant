import os
import tempfile

# Point the app at a throwaway data dir before any app module reads settings.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ara-test-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)
