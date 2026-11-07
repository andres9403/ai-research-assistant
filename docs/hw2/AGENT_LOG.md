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

**Agent result (summary of its report):** 352 tests (225 test functions + parametrisation)
in 10 files plus `conftest.py`/`helpers.py`; `348 passed, 4 xfailed in 3.38s`. It flagged
four suspected application bugs as strict xfails (DOI `"doi: 10.x"` normalisation, a
whitespace-only title accepted on save, a dead "late first heading" branch in
`split_sections`, a failed retry download demoting a ready paper to `no_pdf`) and one
it chose not to test (footnote stripping turns "YOLOv3" into "YOLOv"). It listed as
untested: FastEmbedder, the socket-level download guard, concurrency, frontend serving,
`add_missing_columns`, and ligature extraction through a real PDF.

## Round 1 — coverage report + test review (2026-11-07)

Strategy: execution feedback (the `term-missing` coverage report) plus a human-style
code review of the tests' assertions. Mutation results were deliberately withheld
until Round 2.

**Prompt (verbatim):**

```text
Round 1 of test improvement: here is the coverage report for your suite plus a code review of the tests. Please improve backend/tests_hw2/ accordingly (same constraints as before: don't touch backend/app/ or anything outside tests_hw2/, don't read backend/tests/, don't commit, mock all network/LLM).

EXECUTION FEEDBACK — `pytest tests_hw2 --cov=app --cov-branch --cov-report=term-missing`: 348 passed, 4 xfailed; TOTAL 96% (1960 stmts, 64 missed; 468 branches, 28 partial). Files below 100%:
  app/db.py            91%  missing 48-49 (add_missing_columns: ALTER TABLE for columns an older DB lacks)
  app/services/embeddings.py 70%  missing 47-60, 63-68, 75 (FastEmbedder / get_embedder)
  app/services/http.py 88%  missing 34-35 (DNS gaierror -> ConnectError), 59-61 (connect_tcp falls back to the next public address, then re-raises)
  app/services/llm.py  94%  missing 140-161 (OpenAIProvider._errors mapping of SDK errors, OpenAIProvider.check), 348
  app/services/pipeline.py 92% missing 87-88, 137-139 (upload rolls back when writing the PDF raises OSError), 191, 213-218 (embed(): EmbeddingError and unexpected errors are swallowed/rolled back so the paper stays usable)
  app/services/pdf.py  94%  missing 162, 167, 170-171, 186-188, 304, 349-350
  app/services/metadata.py 97%, search.py 98%, chat.py 99%, retrieval.py 99%, main.py 97% (47->49), settings router 98% (57), config.py 97% (40)

REVIEW FINDINGS (test correctness / assertion strength):
1. test_upload_and_pipeline.py::test_attach_pdf_processes_it_and_fills_only_blank_fields is misnamed relative to what it checks: the paper has abstract=None and year=None, so nothing shows that a NON-blank field survives. Its title assertion is vacuous (fill_missing never touches title). The requirement is "the PDF's metadata only fills the paper's blank fields" — test that existing abstract/year/authors/doi are kept while blank ones are filled.
2. 17 tests assert only a status code. Many 404/422 ones are fine, but several error-path tests should also assert the absence of side effects, e.g.: test_attach_while_busy_is_409 (old PDF/chunks/status untouched), test_question_on_paper_without_text_is_409 and test_question_without_llm_is_503 (no ChatMessage saved, LLM never called), test_get_pdf_with_dangling_path_is_404, test_patch_validates_fields (paper unchanged after a rejected PATCH), test_upload_requires_a_file (no paper created). Review the full list and strengthen where a side effect matters.
3. Your report notes FastEmbedder, the download guard's socket path and add_missing_columns were left untested. Cover them now where it can be done without network or a model download (e.g. monkeypatch the fastembed class / socket.getaddrinfo / the parent connect_tcp; build an old-schema SQLite table by hand and call init_db). 

Goal: stronger, behaviour-focused assertions and coverage of the important missing paths — not test count. Don't add assertions that only exist to execute lines. Keep the 4 strict xfails. When done, report: what you changed per file and why, the new pytest result line, and anything you still chose not to cover.
```
