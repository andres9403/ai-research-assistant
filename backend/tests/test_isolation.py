import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_suite_ignores_external_database_url(tmp_path):
    # A DATABASE_URL from the shell or .env must never be the one the tests touch.
    victim = tmp_path / "victim.db"
    with sqlite3.connect(victim) as conn:
        conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO papers (title) VALUES ('keep me')")

    env = {**os.environ, "DATABASE_URL": f"sqlite:///{victim}"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_health.py"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(victim) as conn:
        rows = conn.execute("SELECT title FROM papers").fetchall()
    assert rows == [("keep me",)]
