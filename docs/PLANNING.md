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

## 6. Later decisions

### Issue #8 — AI without a manual API key (October 10, 2026)

Before this change, summaries, Q&A and the LLM metadata pass worked only after the user got an Anthropic or OpenAI key, pasted it into `.env` and restarted the backend. Issue #8 asked for a lower-friction path.

| Option | Privacy | Operating cost | Setup time | Feature quality | Verdict |
|---|---|---|---|---|---|
| Local model through Ollama | Papers never leave the machine | None | Install Ollama and pull a 3.3 GB model, a few minutes | Weaker than Claude Haiku or GPT mini, and slow without a GPU | **Chosen** |
| In-app key entry | Paper text still goes to the provider | The user's own API bill | Still needs a provider account and key, but no file editing or restart | Same as today | **Also chosen**, after review: users who have a key should be able to add it in the app |
| Hosted backend or authenticated proxy | Paper text goes to the provider through a third party | Someone pays for every user's requests, plus hosting | None for the user | Same as today | Not chosen: needs a funded key, authentication, rate limits and an ongoing deployment, which is beyond a project that "does not need to be production-ready" |
| Demo mode with canned output | Nothing leaves the machine | None | None | No real AI; the output is simulated | Not chosen: it would mislead in the demo video |

How the chosen option works:

- Ollama is a third provider next to Anthropic and OpenAI. It needs no key. The backend asks Ollama for its model list (`GET /api/tags`, 2-second timeout) on every AI request and every settings check. Ollama is offered only when it is running **and** the configured model is pulled. Pulling a model therefore needs no backend restart, and the in-app **Check again** button picks it up.
- Requests use Ollama's native `/api/chat` with the JSON schema as `format`, as structured outputs do on the other two providers. They do not use Ollama's OpenAI-compatible endpoint, because that endpoint cannot set `num_ctx`. Ollama's default context would silently cut the ~8k-token summary input short. The app asks for a 16k-token context.
- The default model is `gemma3:4b` (3.3 GB), overridable with `OLLAMA_MODEL`. Three CPU-sized models were run on the tutorial's five questions about *Attention Is All You Need*, with the app's own chat prompt. `llama3.2:3b` and `qwen2.5:3b` gave sensible answers but cited no excerpt in any of them, so the app rejected all ten (Q&A refuses uncited answers). `gemma3:4b` cited correctly in all five.
- Ollama has its own timeout, `OLLAMA_TIMEOUT` (900 s), because CPU inference is far slower than the hosted APIs. Its output is capped at 1,024 tokens. Answers and summaries need a few hundred, and in testing a small model occasionally repeated itself until the timeout. With the cap, that run ends in minutes as "cut off" instead.
- The `.env` keys work exactly as before. When a key is set, its provider stays the default; Ollama is listed last.
- Ollama has no credential. `GET /api/settings/llm` describes providers by name and model only, so no key ever reaches the browser.

In-app key entry, added after review of the first version:

- **API keys** in the header, and the Summary and Chat empty states, take an Anthropic or OpenAI key. `PUT /api/settings/llm/keys/{provider}` checks it with the provider's free `models.retrieve` call for the configured model, then writes it to the git-ignored `.env` and applies it to the running backend. No restart is needed, and the key is kept after one.
- The key is saved to `.env` rather than held in backend memory, so it works exactly like a hand-edited key and survives restarts. The file is rewritten atomically with owner-only permissions (600), keeping every other line.
- Only text shaped like a key (letters, digits, `-` and `_`) is accepted, so a pasted newline can't add or change other settings in `.env`. The key is a `SecretStr` in the request model. It is never returned, logged or kept in browser storage, and errors never echo it.
- A variable with the same name in the shell still overrides `.env`, as pydantic-settings does for every setting; the README's troubleshooting table says so.

Measured end to end on the development machine (WSL2, 12 CPU threads, no GPU), with no key set and *Attention Is All You Need* uploaded:

| Step | `gemma3:4b` on the CPU |
|---|---|
| Speed | Reads prompts at 22–27 tokens/s, writes at 6–7 tokens/s |
| Upload with the first-page metadata pass | 58 s (the upload request waits for it) |
| Summary (6,247 tokens in, 568 out) | 326 s |
| Question with citations (~2,200 tokens in) | 115 s, cited correctly |

Loading the model before the first request adds about a minute. A machine with a supported GPU or Apple silicon is many times faster. The upload wait is the most noticeable cost. Moving the metadata pass to a background task would remove it, but that changes the upload flow for every provider, so it was left for a follow-up.

### Issue #13 — Background knowledge in Q&A (October 10, 2026)

Q&A answered only from the retrieved excerpts, so it refused questions about concepts a paper uses but doesn't define. Asked "what is McCormick envelopes" about a paper on convexifying Kolmogorov-Arnold Networks, it replied only that the excerpts don't define them. The user asked for the model's general knowledge to answer such questions.

| Option | Verdict |
|---|---|
| Labelled background: a separate, uncited part of the answer, shown apart from the paper-based answer | **Chosen** |
| Free mixing: drop the excerpts-only rule and let uncited sentences stand | Not chosen: you could no longer tell paper claims from the model's own |
| A per-question "use general knowledge" toggle | Not chosen: an extra control for something the model can decide per question |

How it works:

- The structured answer has a third field, `background`. It holds general knowledge the question needs and the excerpts don't give, and is empty otherwise. The prompt forbids describing the paper or its results there.
- `answer` stays grounded as before. An uncited answer is still rejected unless `found` is false, and background doesn't excuse it. Any `[n]` markers in background are stripped, because they would point at nothing.
- Background is saved in a new nullable `chat_messages.background` column, added to older databases at startup. It goes into the conversation history for follow-ups and counts toward the history budget.
- The Chat tab shows it in a separate block labelled "Background · from the model's general knowledge, not the paper".
- It needs no extra LLM call. On the McCormick question with Claude Haiku, the answer used 4,911 tokens in and 299 out, cited two passages, and added a one-paragraph background. "What are the main results?" on the same paper got five citations and no background.

### Issue #12 — Semantic Scholar key from the app (October 10, 2026)

Without a key, Semantic Scholar often answers HTTP 429, so many searches fall back to arXiv, which ranks exact titles worse and lacks the venue, paper-type and citation-sort filters. Adding a key meant editing `.env` and restarting, and the 429 note didn't say a key would help.

How it works:

- **API keys** in the header offers **Semantic Scholar (search)** next to the LLM providers. `PUT /api/settings/search/keys/semantic_scholar` reuses the issue #8 path in `services/api_keys.py`: the same key-shape check, then the same atomic, owner-only write to `.env`, then the running settings are updated. Search and the upload metadata lookup read the key on every request, so no restart is needed.
- The check is one `GET /graph/v1/paper/arXiv:1706.03762?fields=paperId` with the key in `x-api-key`. A 2xx accepts the key. 401 or 403 rejects it. A 429, another error status or no connection means the key couldn't be confirmed, and nothing is saved in any of these cases; the user is told to try again.
- Search settings live under their own route, `GET /api/settings/search`, which says only whether a key is set. They are kept out of `GET /api/settings/llm` because Semantic Scholar is not an LLM provider.
- `SearchResponse.suggest_s2_key` is true when Semantic Scholar answered 429 and no key is set. The results note then links to the API keys menu with Semantic Scholar already chosen. A 429 with a key set doesn't suggest one, because a key's own limit (1 request per second) was hit.
- The key stays optional. Semantic Scholar approves keys after a request form and prioritises academic, nonprofit and government requests, so search keeps working without one, through the keyless shared limit or the arXiv fallback.

### Issue #7 — Running and checking the app as an outside reviewer (October 10, 2026)

The quick start needed a virtualenv, two installs and two terminals before anything was on screen, and a reviewer couldn't tell which checks needed an API key. On macOS the default `python3` can be 3.9, too old for the backend.

How it works:

- `start.sh` and `start.ps1` find Python 3.11+ (trying `python3.13`, `python3.12` and `python3.11` before `python3`, or the `py` launcher on Windows), create `backend/.venv`, install the dependencies, build the frontend and start uvicorn. A copy of `requirements.txt` in the venv records the last successful install, so later runs skip `pip` unless it changed.
- FastAPI serves `frontend/dist` at `/` when it exists. `create_app()` mounts it after every router, so `/api` and `/docs` still reach FastAPI. The frontend uses hash routes, so no fallback to `index.html` for unknown paths is needed. `npm run dev` stays the development path.
- GitHub Actions runs the backend suite on Python 3.11 and 3.13, the frontend tests and build on Node 18 and 24, and the start scripts on Linux, macOS and Windows with each runner's preinstalled Python and Node. The suite already mocks every external service, so CI needs no secrets.
- The README opens with a **For reviewers** section: the one command, and a table mapping each requirement to a check and whether it needs a key.
- **Docker stays out (Q11).** The start scripts meet the goal of one command with only Python and Node installed. Docker would add an image to maintain and volume handling for `data/` and the `.env` that the app writes keys to. It can be added later beside the scripts if a reviewer can't install Python or Node.
