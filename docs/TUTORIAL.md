# Tutorial and demo script

This walkthrough covers every feature of the AI Research Assistant, in the order a new user would meet them. It is also the script for the demo video: each scene lists what to **do** on screen and what to **say**, and the whole video runs about seven minutes.

The demo uses two papers:

- **Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks** (Lewis et al., 2020). It is found through search, and its open-access PDF is downloaded automatically.
- **BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding** (Devlin et al., 2018). It is uploaded as a PDF from disk.

## Before you record

1. Set up the app by following the [README](../README.md#quick-start), with at least `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `.env`. Set both if you want to show the provider switch in scene 7.
2. Start with an empty library. Stop the backend, move the old data aside while keeping the embedding model, and start the backend again:

   ```bash
   mv data data-before-demo
   mkdir data && cp -r data-before-demo/models data/
   ```

   To go back to your own library after recording, stop the backend and swap the folders again.
3. Download the BERT PDF from <https://arxiv.org/pdf/1810.04805> and save it somewhere easy to find, such as the desktop, as `bert.pdf`.
4. Open <http://localhost:5173> in a browser window of about 1200 × 800 pixels, and zoom in to 110–125% so text is readable in the video.
5. Make sure the header shows the **AI model** dropdown. If it says **AI off: no LLM key**, the key is missing from `.env`, or the backend was not restarted after you added it.

## Scene 1 — Introduction (30 s)

**Do:** Show the empty Search page.

**Say:** "This is an AI research assistant. It finds research papers, keeps them in a local library, and uses an LLM to summarize them and answer questions. The answers are based on each paper's full text, not just its abstract. It runs on your own machine, with a FastAPI backend, a React frontend and a SQLite database."

## Scene 2 — Search by topic (45 s)

**Do:** Type `retrieval augmented generation` in the search box and press Enter. Scroll slowly through a few results. Click **Show more** on one abstract.

**Say:** "Search goes to the Semantic Scholar API. Each result shows the title, authors, year, abstract and a link to the paper, and the PDF tag marks papers that have an open-access PDF. If Semantic Scholar is rate-limited, the app falls back to arXiv automatically, and the line above the results says which source answered."

> The line above the results reads, for example, *20 results from Semantic Scholar*, or *20 results from arXiv · Semantic Scholar unavailable (HTTP 429)*. Either is fine for the demo.

## Scene 3 — Save papers (45 s)

**Do:** Replace the query with `retrieval-augmented generation for knowledge-intensive NLP tasks` and search. Click **Save** on the first result, the Lewis et al. paper. Its button turns into **✓ In library**. Save one more result too.

**Say:** "Saving a paper stores it in the local database. Duplicates are caught by DOI or Semantic Scholar ID, and then by title, so saving the same paper twice just links to the copy you already have. When a paper has an open-access PDF, the app starts downloading it in the background."

## Scene 4 — The library (1 min)

**Do:** Click the **Library** tab. Point at the status tags as they change from **Downloading PDF…** to **Processing PDF…** to **Full text ✓**. This takes a few seconds per paper. Type `lewis` in the filter box, then click **Clear**. Delete the second paper you saved, and confirm.

**Say:** "The library lists everything you've saved. While a PDF downloads, the app extracts its text locally with PyMuPDF. It removes headers, footers and the reference list, splits the text into sections and chunks, and embeds the chunks with a small local model. Once the tag says Full text, the AI features are ready. You can filter by text, source and year. The library is stored in SQLite, so it survives page refreshes and restarts."

**Do (optional):** Reload the page to show that the library is still there.

**Do:** Click the **Search** tab again. The results are still there, and the paper you deleted can be saved again.

## Scene 5 — Upload a PDF (1 min)

**Do:** Back in **Library**, click **Upload PDF** and choose `bert.pdf`. The button reads **Extracting bert.pdf…**, and after a few seconds the paper's page opens.

**Say:** "You can also upload a PDF from your own disk. The app reads the title, authors, year and abstract from the PDF itself. It sends only the first page's text to the LLM to correct them, then checks the result against Semantic Scholar and arXiv. Here it found the paper's arXiv identifier, so the metadata is exact."

**Do:** Point at the green banner, the extracted title, authors and abstract, and the note *Metadata was matched on arXiv by the paper's identifier.* Click **Edit metadata** to show the form, then click **Cancel**.

**Say:** "Every field stays editable, in case the extraction gets something wrong."

## Scene 6 — What the AI reads (30 s)

**Do:** Go back to the library and open the Lewis et al. paper. Click the **Extracted text** tab. Read out the summary line, for example *19 pages → 29 sections, 40 chunks, about 15k tokens*. Expand one section.

**Say:** "This tab shows exactly what the AI works from: the cleaned text, split into sections and chunks. The whole paper is about fifteen thousand tokens, and the app never sends all of it to the model."

## Scene 7 — Structured summary (1 min)

**Do:** Click the **Summary** tab, then **Generate summary**. It takes about ten seconds. Scroll through the TL;DR, Problem, Approach, Data, Results and Limitations. Expand the line under the summary that starts with **Written by**.

**Say:** "The summary is structured: a one-line TL;DR, then the problem, approach, data, results and limitations. It's written from the paper's most informative sections, such as the abstract, conclusion, introduction and results, up to about eight thousand tokens. This line shows which sections were used and how many tokens it cost. The summary is saved, so opening it again is free."

**Do (only if both API keys are set):** Switch the **AI model** dropdown in the header to the other provider. The button changes to **Regenerate with …**. Click it.

**Say:** "The app supports both Anthropic and OpenAI. The dropdown lists only the providers that have a key, and you can switch at any time."

## Scene 8 — Ask questions (2 min)

**Do:** Click the **Chat** tab. Point at the five example questions, then click them one after another, waiting for each answer:

1. *What problem does this paper address?*
2. *What method or approach do the authors propose?*
3. *What data or experiments do they use to evaluate it?*
4. *What are the main results?*
5. *What limitations or future work do the authors mention?*

Each answer takes a few seconds.

**Say (while the first answer loads):** "For each question, the app embeds the question locally, finds the passages most similar to it, and sends only those passages to the model, together with the abstract and conclusion. That's about three thousand tokens of the paper, not the whole paper."

**Do:** On the *main results* answer, click a blue citation number. The cited passage opens below the answer. Click it again to close it.

**Say:** "Every answer cites the passages it used, with section and page, so you can check each claim against the paper. The token count under each answer shows what it cost."

**Do:** Type a follow-up question of your own, such as `How does RAG-Token differ from RAG-Sequence?`, and press Enter.

**Say:** "Follow-up questions work too, because the recent conversation is sent along. The conversation is saved with the paper, so it's still here the next time you open it."

## Scene 9 — Wrap-up (30 s)

**Do:** Show the README on GitHub, scrolling past the token table and the architecture diagram.

**Say:** "To recap: search through a public API, a local library that persists, PDF upload with metadata extraction, and summaries and Q&A grounded in the full text, with citations. Text processing and embeddings run locally, and the LLM sees only the sections it needs. The README covers setup from a fresh clone, and the backend has an automated test suite. Thanks for watching."

## If something goes wrong while recording

| What you see | What to do |
|---|---|
| Search shows only arXiv results | Expected when Semantic Scholar is rate-limiting. Carry on, since the same paper comes up first |
| A saved paper stays on **No PDF** | Its PDF link failed. Open the paper and use **Upload PDF**, or pick a different paper |
| The first question pauses for several seconds | The embedding model is loading. Later questions are faster |
| An answer shows an error from the provider | Check the key and your account credit, then click **Ask** again. The question is kept in the box |
