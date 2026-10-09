# AI Research Assistant

A web app for searching, saving and uploading research papers, with LLM summaries and Q&A grounded in each paper's full text. The stack is a FastAPI backend, a React (Vite) frontend and SQLite.

**Read `docs/PLANNING.md` first.** It records every design decision, the architecture, and the milestone plan (M0–M5). Don't revisit settled decisions unless the user asks.

## Workflow

- Build one milestone at a time, each on its own branch (`m1-search-library`, `m2-pdf-pipeline`, …).
- Finish each milestone with passing tests, a live smoke test, and a PR opened with `gh`.
- Then stop and wait for the user's review. Never merge a PR or start the next milestone without their approval.

## Commands

```bash
# Backend (from backend/)
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/python -m pytest -q

# Frontend (from frontend/)
npm install
npm run dev      # http://localhost:5173, proxies /api to :8000
npm run build
```

## Conventions

- Configuration comes from `.env` at the repo root, loaded by `backend/app/config.py`. Keep `.env.example` in sync when adding settings.
- Runtime data lives in `data/` (`app.db`, `pdfs/`), which is git-ignored.
- Tests set the `DATA_DIR` env var to a temp dir in `tests/conftest.py`, before any app import. Mock the LLM and external HTTP in tests; never call real APIs.
- Node is v18, so stay on Vite 5.x.
- Every API route lives under `/api` and has a router in `backend/app/routers/`. Business logic goes in `backend/app/services/`.
- Keep token use low: process PDFs locally with PyMuPDF, and send the LLM only the selected sections or retrieved chunks, never whole papers.
