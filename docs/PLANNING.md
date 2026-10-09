# Planning Record

This is a record of the design session held before any code was written (October 9, 2026). It lists every decision taken, the option recommended, and the option chosen. The approved plan it produced drives the M0–M5 milestones.

## 1. Starting brief

Build an AI research assistant that can:

- search research papers by keyword or topic through a public API, showing title, authors, year, abstract and a link;
- save selected papers to a local database that persists across restarts and page refreshes, and browse that library;
- upload PDFs, extracting title, authors, year and abstract so uploads display like search results;
- summarize a selected paper and answer natural-language questions about it, using the paper's **actual content**, not just its metadata;
- keep LLM token use low by extracting text from PDFs locally and sending only the relevant content.

Deliverables: a GUI with frontend and backend, at least one external API, a local database, PDF processing, LLM integration, a GitHub repo under `andres9403`, a README, and a usage tutorial to serve as the script for a demo video. The app needs to work end to end; it does not need to be production-ready.

## 2. Environment facts checked before asking questions

| Fact | Finding |
|---|---|
| Python | 3.12.7 (Anaconda) |
| Node | v18.19.1, so Vite 5 rather than Vite 6+ |
| GitHub CLI (`gh`) | Not installed |
| LLM API keys | None set in the environment |
| Working directory | An unrelated repo, so the app gets its own folder |

## 3. Decisions, round by round

### Round 1 — Foundations

| # | Question | Recommended | Chosen |
|---|---|---|---|
| Q1 | Where should the project live? | New folder `~/ai-research-assistant`, public repo `andres9403/ai-research-assistant` | Recommended |
| Q2 | How should the GitHub repo be created (`gh` is missing)? | User installs `gh` and runs `gh auth login`; Claude creates the repo and pushes | Recommended |
| Q3 | Which tech stack? | FastAPI + React (Vite) | Recommended |
| Q4 | Which LLM provider? | Anthropic, behind a swappable interface | **Both Anthropic and OpenAI, selectable** |
| Q5 | Which paper search API? | Semantic Scholar, with arXiv as fallback | Recommended |
| Q6 | How do saved search results get full text? | Download the open-access PDF automatically; otherwise offer "Upload PDF to enable AI" | Recommended |
| Q7 | How do we keep token use low? | PyMuPDF extraction and cleaning, section chunks, capped key sections for summaries, local-embedding retrieval for Q&A, cached summaries | Recommended |
| Q8 | How is metadata extracted from uploads? | PDF metadata and first-page heuristics, then an LLM pass on the first page only, then a Semantic Scholar lookup; fields stay editable | Recommended |
| Q9 | Which database? | SQLite through SQLAlchemy; single user, no login | Recommended |
| Q10 | What style of Q&A? | A saved chat thread per paper, with answers citing sections | Recommended |
| Q11 | What extra deliverables? | `docs/TUTORIAL.md` video script, pytest tests, no Docker | Recommended |

### Round 2 — Consequences of round 1

| # | Question | Recommended | Chosen |
|---|---|---|---|
| Q12 | Where is the LLM provider selected? | A default in `.env` plus a dropdown in the UI that lists only providers with keys | Recommended |
| Q13 | Which default models? | Cheap, fast tiers (Claude Haiku, GPT mini), overridable in `.env` | Recommended |
| Q14 | Which embedding library? | `fastembed` (ONNX, about 100 MB) rather than `sentence-transformers` (needs PyTorch, about 2 GB) | Recommended |
| Q15 | Accept the remaining round-1 recommendations? | Accept all | Recommended |
| Q16 | How is the Python environment set up? | Plain `venv` + `requirements.txt` | Recommended |

### Round 3 — UI and library behaviour

| # | Question | Recommended | Chosen |
|---|---|---|---|
| Q17 | What is the UI layout? | Three views: Search, Library, and Paper detail (with Summary and Chat tabs) | Recommended |
| Q18 | What format do summaries use? | Structured: TL;DR, Problem, Approach, Data, Results, Limitations | Recommended |
| Q19 | How are duplicate papers handled? | Detect by DOI or Semantic Scholar ID, then by normalised title | Recommended |

### Round 4 — Architecture review and delivery

The first plan draft did not have an explicit architecture review, and it built everything before a single push. These questions fixed both gaps.

| # | Question | Recommended | Chosen |
|---|---|---|---|
| Q20 | Is the architecture approved? | Approve as shown | Approved together with the plan |
| Q21 | How should delivery be split? | Vertical-slice milestones M0–M5 | Recommended |
| Q22 | When should the user review? | Pause for review after every milestone | Recommended |
| Q23 | What git workflow? | One feature branch and PR per milestone | Recommended |

## 4. Resulting architecture

```
┌──────────────── React (Vite) frontend ────────────────┐
│  Search view │ Library view │ Paper detail view        │
│              │              │   ├─ Summary tab         │
│              │              │   └─ Chat tab            │
│  Header: LLM provider dropdown                         │
└───────────────────────────┬───────────────────────────┘
                            │ REST / JSON  (/api/*)
┌───────────────────────────▼───────────────────────────┐
│ FastAPI routers: search · papers · upload · ai ·       │
│                  settings                              │
├────────────────────────────────────────────────────────┤
│ Services                                               │
│  SearchService ────> Semantic Scholar API / arXiv API  │ (external)
│  PdfPipeline   ────> PyMuPDF: extract → clean →        │
│                      section chunks                    │
│  MetadataExtractor:  heuristics → LLM (1st page only)  │
│                      → Semantic Scholar lookup         │
│  Retriever     ────> fastembed vectors, NumPy cosine   │
│                      top-k                             │
│  AIService     ────> LLMProvider { Anthropic | OpenAI }│ (external)
├────────────────────────────────────────────────────────┤
│ Storage: SQLite via SQLAlchemy                         │
│   papers · chunks · summaries · chat_messages          │
│   data/pdfs/  (PDF files)                              │
└────────────────────────────────────────────────────────┘
```

Each user action moves through the system like this:

1. **Search.** Semantic Scholar is queried, falling back to arXiv. Results come back as normalised cards and nothing is stored.
2. **Save.** The paper is checked for duplicates and inserted. A background task downloads its open-access PDF, extracts, cleans and chunks the text, and embeds the chunks; the paper's status then becomes `ready`.
3. **Upload.** The PDF is stored and goes through the same extract, clean, chunk and embed steps. Metadata is extracted and the user can edit it.
4. **Summary.** The key sections, up to about 8k tokens, go to the selected LLM. The structured summary that comes back is cached.
5. **Question.** The question is embedded and the most relevant chunks are retrieved. Those chunks plus recent chat history go to the LLM, which answers with citations; the exchange is saved.

## 5. Delivery milestones

| Milestone | Scope | Done when |
|---|---|---|
| M0 Skeleton | Repo, FastAPI health endpoint, Vite app, database init, `.env.example`, first push | The page shows "backend OK" and the repo is on GitHub |
| M1 Search + Library | Search, save, list, delete, filter, duplicate detection | A saved paper survives a restart |
| M2 PDF pipeline | Open-access PDF download, upload and attach, extract, clean, chunk, metadata | An uploaded PDF shows its extracted metadata |
| M3 LLM layer | Both providers, provider dropdown, LLM metadata pass, structured summaries | A summary is generated from the paper's content |
| M4 Q&A | Embeddings, retrieval, chat with citations | The five example questions are answered with citations |
| M5 Finish | Polish, full README, `docs/TUTORIAL.md` | A fresh clone runs by following the README alone |
