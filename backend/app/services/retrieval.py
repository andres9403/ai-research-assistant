"""Finding the chunks of a paper that answer a question: local vectors, NumPy cosine, top-k.

Only a few chunks (about 3k tokens at most) go to the LLM, never the whole paper:
two anchors, the abstract and the start of the conclusion, which answer questions
about the paper as a whole, plus the chunks most similar to the question. Small
embedding models barely tell chunks apart on questions like "what problem does
this paper address?", so a question that names a kind of content (results,
data, limitations, …) also nudges chunks from the sections that usually hold it.
Chunks processed before embeddings existed are embedded on first use.
"""

import re
import threading
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from sqlalchemy.orm import Session

from app.models import Chunk, Paper
from app.services import embeddings

TOP_K = 6  # retrieved by similarity, on top of the anchors
CONTEXT_BUDGET_TOKENS = 3000

ANCHOR_SECTIONS = (r"^abstract\b", r"conclu")
HINT_BONUS = 0.03  # about half the gap between a typical best chunk and the median one
# (what the question asks about, the sections that usually answer it)
SECTION_HINTS = [
    (r"problem|motivat|challenge|address|goal|aim\b|purpose", r"abstract|introduction|motivation"),
    (r"method|approach|propos|architecture|algorithm|technique|framework|\bhow does",
     r"method|approach|model|architecture|framework|design|propos|algorithm"),
    (r"data|benchmark|experiment|evaluat|corpus|participant|setup|baseline",
     r"data|experiment|evaluation|setup|benchmark|training|implementation"),
    (r"result|perform|accura|score|outperform|finding|improv|achiev|compar",
     r"result|experiment|evaluation|finding|ablation|comparison"),
    (r"limitation|future|weakness|drawback|shortcoming|caveat|open (question|problem)",
     r"limitation|conclu|discussion|future"),
]


@dataclass
class Retrieved:
    chunk: Chunk
    score: float


_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def ensure_embeddings(session: Session, paper: Paper) -> None:
    """Embed the chunks of `paper` that have no vector yet, and save them.

    The background task after an upload and an early question may both get here;
    the second to arrive waits, rereads what the first saved, and has nothing left to do.
    """
    with _locks_guard:
        lock = _locks[paper.id]
    with lock:
        dim = embeddings.get_embedder().dim
        for chunk in paper.chunks:
            if not embeddings.has_embedding(chunk, dim):
                session.refresh(chunk, ["embedding"])
        if embeddings.embed_chunks(paper.chunks):
            session.commit()


def anchors(chunks: list[Chunk]) -> list[Chunk]:
    """The opening chunk of the abstract (or of the paper) and of the conclusion, if any."""
    out: list[Chunk] = []
    for i, pattern in enumerate(ANCHOR_SECTIONS):
        found = next((c for c in chunks if re.search(pattern, c.section, re.I)), None)
        if found is None and i == 0:
            found = chunks[0]
        if found is not None and found not in out:
            out.append(found)
    return out


def section_bonus(question: str, chunks: list[Chunk]) -> np.ndarray:
    bonus = np.zeros(len(chunks), dtype=np.float32)
    lowered = question.lower()
    for asks, answered_in in SECTION_HINTS:
        if re.search(asks, lowered):
            bonus += [HINT_BONUS if re.search(answered_in, c.section, re.I) else 0.0 for c in chunks]
    return np.minimum(bonus, HINT_BONUS)


def retrieve(
    session: Session,
    paper: Paper,
    queries: list[str],
    k: int = TOP_K,
    budget: int = CONTEXT_BUDGET_TOKENS,
) -> list[Retrieved]:
    """The anchors plus the `k` chunks that best match `queries`, within `budget` tokens, in paper order.

    `queries[0]` is the question. A chunk's score is its best cosine similarity
    across the queries, so a follow-up can be searched both on its own and joined
    to the question before it, plus the section bonus the question earns it.
    """
    chunks = paper.chunks
    if not chunks or not queries:
        return []
    ensure_embeddings(session, paper)

    matrix = np.stack([embeddings.from_bytes(c.embedding) for c in chunks])
    query_vectors = np.stack([embeddings.embed_query(q) for q in queries])
    scores = (matrix @ query_vectors.T).max(axis=1) + section_bonus(queries[0], chunks)

    position = {id(c): i for i, c in enumerate(chunks)}
    chosen: list[Retrieved] = []
    used = 0
    for chunk in anchors(chunks):
        if used + chunk.n_tokens <= budget:
            chosen.append(Retrieved(chunk, float(scores[position[id(chunk)]])))
            used += chunk.n_tokens
    taken = {id(r.chunk) for r in chosen}
    retrieved = 0
    for i in np.argsort(-scores, kind="stable"):
        chunk = chunks[i]
        if retrieved == k:
            break
        if id(chunk) in taken or used + chunk.n_tokens > budget:
            continue
        chosen.append(Retrieved(chunk, float(scores[i])))
        used += chunk.n_tokens
        retrieved += 1
    chosen.sort(key=lambda r: r.chunk.ordinal)
    return chosen
