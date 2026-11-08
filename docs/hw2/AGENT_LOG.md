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

**Agent result (summary of its report):** `375 passed, 4 xfailed in 2.95s`; coverage
96% → 99%. Split the misleading attach test into "fills blank fields" and "never
overwrites existing fields"; error-path tests in `test_ai_api.py`, `test_papers_api.py`,
`test_settings_api.py` and the upload/attach tests now assert no side effects (no
message/summary saved, LLM not called, `.env` not written, PDF/chunks untouched). New
`test_app_infrastructure.py` (old-schema migration, frontend serving, FastEmbedder with
a stand-in `fastembed` module). New tests for the download guard's DNS/fallback paths,
upload rollback on `OSError`, embedding-failure fallbacks, OpenAI error mapping. It also
found and fixed two of its own tests that passed for the wrong reason (an Ollama
"by name" test hitting the generic no-LLM path; a chunk-merge test whose input never
triggered a merge).

## Round 2 — mutation feedback + bug triage + requirements audit (2026-11-07)

Strategy: a different kind of context from Round 1. (A) mutation-testing results for
the 2 surviving seeded faults, framed as "fix the class of weakness"; (B) human triage
of the agent's suspected bugs, confirming them and adding a fifth it had noticed but not
tested; (C) a requirements-traceability audit against `docs/PLANNING.md`; (D) a
maintainability pass (private-helper coupling, redundant tests). Before sending it, an
extra held-out set of 18 seeded faults was written and **never shown to the agent**, to
detect overfitting to the 40 it was told about.

**Prompt (verbatim):**

```text
Round 2 of test improvement — a different kind of feedback this time: mutation-testing results, triage of the bugs you reported, and a requirements audit. Same constraints as before (only edit backend/tests_hw2/, don't read backend/tests/, don't touch backend/app/, don't commit, mock all network/LLM).

A. MUTATION TESTING FEEDBACK. We seeded 40 single-line faults into backend/app (each one breaks a stated requirement) and ran your current suite against each; it detected 38. These two mutants SURVIVED (all tests still passed):
  - app/services/library.py, list_papers():  `stmt.where(Paper.year <= year_to)`  →  `stmt.where(Paper.year < year_to)`
  - app/services/metadata.py:  `TITLE_MATCH_THRESHOLD = 0.9`  →  `TITLE_MATCH_THRESHOLD = 0.5`
Don't just write a test aimed at each exact mutation: work out what weakness in the suite let each survive (what kind of input your tests never use) and fix that class of weakness across the suite, wherever the same blind spot appears (e.g. other range filters, limits, size caps, budgets, thresholds).

B. BUG TRIAGE (I checked each of your xfails against the code and the UI):
  1. normalize_doi "doi: 10.x" — confirmed real bug. Keep the strict xfail.
  2. Whitespace-only title accepted by POST /api/papers — confirmed real (inconsistent with the edit rule). Keep.
  3. split_sections late-first-heading branch is dead (front matter always has page_start=1) — confirmed real. Keep.
  4. Failed retry demotes a ready paper to no_pdf — confirmed real at the API level, but note the frontend only shows the "Download open-access PDF" button when has_pdf is false, so a user can only reach it via the API. Keep the xfail and make its reason say that.
  5. The footnote-mark stripping you mentioned but didn't test: I confirmed `_clean_title("YOLOv3: An Incremental Improvement")` returns "YOLOv: An Incremental Improvement" and "ResNet50" loses its digits. Footnote marks on author names are intended; mangling model names in titles is not. Add a strict xfail that shows this through the public behaviour (a PDF upload whose heuristic title contains a model name like YOLOv3, with no LLM and offline lookups), not by calling the private helper.

C. REQUIREMENTS AUDIT. Re-read docs/PLANNING.md (the design decisions and milestone acceptance criteria). For each user-visible requirement it states, check there is a test that would fail if it were broken. Add tests for any requirement without one; if a requirement can't be tested here, say why in your report.

D. MAINTAINABILITY. Your tests call private helpers in a few places (llm._parse, chat._numbers, pdf._normalize, metadata._split_names / _looks_like_name / _clean_title, pipeline._title_from_filename, client._transport / _trust_env). Where the same behaviour can be checked through a public function or the API, switch to that; keep a private call only when there's no reasonable public route, and say which ones you kept. Also remove tests that are redundant with another test (same behaviour, same input class).

Quality over quantity: removing or merging tests is fine if the suite still catches the same faults. When done, report: what you changed under A–D (with test names), the new pytest result line, and the tests you deleted or merged and why.
```

**Agent result (summary of its report):** `433 passed, 6 xfailed in 3.57s`; coverage
stays at 99%. (A) It diagnosed both survivors as one blind spot, "inputs well inside or
well outside a limit, never at it", and added at-the-limit tests across the suite (year
ranges, query/author/venue length limits, the three 50 MB size caps, chunk target and
minimum sizes, heading length and font-size cut-offs, retrieval/summary/history token
budgets, title-similarity threshold at 0.903 vs 0.889, key length 19/20/400, search
limit 1/50, PATCH year bounds). It seeded 24 extra boundary faults itself in a scratch
copy to check them, which exposed two more of its own weak tests (one asserted
`1 + retrieval.TOP_K`, so it moved with the mutation). (B) It updated bug 4's reason and
added a strict xfail for the "YOLOv3" title bug through the upload API. (C) Its PLANNING.md
audit added tests for 13 untested requirements, e.g. that the library survives a
restart, that the summary sends ≤ 8k tokens and Q&A ≤ 3k tokens of the paper, that keys
are never logged, and that a saved key works without a restart. It listed the
requirements it could not test (real providers, UI, start scripts). (D) It replaced 12 of
13 private-helper references with public routes (end-to-end download-client tests with a
fake DNS/socket layer; ligature extraction through a real PDF with ligature glyphs) and
deleted or merged 24 redundant tests.

## Manual interventions

None in the test code: every line in `backend/tests_hw2/` was written by the generator
agent. The evaluation harness in `backend/hw2_eval/` was written by the orchestrating
Claude Code session and never shown to the generator. The
human-side work was: writing the prompts; running coverage and the mutation harness;
checking each suspected bug against the code and the UI (Round 2, part B); and spotting
the fifth bug (it was in the agent's report but untested) and confirming it by hand.
