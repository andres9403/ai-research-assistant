# HW2 agent interaction log

Every prompt given to the test-generating agent, verbatim, with what came back.
Agent: Claude Code (Claude Opus 5.5), spawned as a fresh sub-agent with no prior
conversation context. It could read the repository (including `CLAUDE.md` and
`docs/PLANNING.md`) but was told not to read the HW1 test suite in `backend/tests/`.

## Part 1 — initial generation (2026-11-07)

**Prompt (verbatim):**

```text
You are working in the repository at /home/andresfel9403/ai-research-assistant — the "AI Research Assistant" web app (FastAPI backend + SQLite, React frontend). Your job is to examine the existing backend application and generate an automated pytest test suite for it.

Requirements:
- Put all tests in a NEW directory: backend/tests_hw2/ (with its own __init__.py and conftest.py).
- Cover the application's main functionality: normal use cases, edge cases, and error-handling behaviour.
- Tests must be executable and contain meaningful assertions about the application's expected behaviour.
- Run them with: cd /home/andresfel9403/ai-research-assistant/backend && .venv/bin/python -m pytest tests_hw2 -q -p no:cacheprovider

Constraints:
- Do NOT open, read, copy, or import anything from backend/tests/ — that is an older suite excluded from this experiment. Build your own fixtures from scratch.
- Do NOT modify any application code under backend/app/ or any other file outside backend/tests_hw2/. Do not commit.
- Never call real network services or LLM APIs; mock them. Point the app at a temporary data directory (DATA_DIR / DATABASE_URL env vars) before any app module is imported, so the real data/ directory and .env are never touched.
- If a test fails and you believe the APPLICATION is wrong (not your test), do not change the app and do not weaken the test: leave it in place, mark it with `@pytest.mark.xfail(strict=True, reason="SUSPECTED BUG: ...")`, and explain it in your report.

When finished, report back: (1) the list of test files with the number of tests in each and what functionality each targets, (2) the final pytest result line, (3) any suspected application bugs with the test that exposes each, (4) anything you deliberately did not test and why.
```

Note: the prompt deliberately says nothing about coverage, mutation testing or the
specific requirements in PLANNING.md, to get a realistic "first ask" baseline.
