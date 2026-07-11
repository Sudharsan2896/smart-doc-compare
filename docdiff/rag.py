"""
Procurement Knowledge Base — RAG (Retrieval-Augmented Generation).

Plain English: you feed in past procurement documents (quotes, POs, contracts,
AMC records). The app breaks each one into small passages and remembers the
"meaning" of every passage. Later you ask a question in ordinary English —
"what warranty did GreenVolt give us last time?" — and the app:

    1. RETRIEVES the handful of passages whose meaning is closest to your question
       (this is the "vector search" every vector database does under the hood).
    2. AUGMENTS a prompt with just those passages.
    3. GENERATES an answer with an LLM that is told to use ONLY those passages and
       to cite them — so it can't make things up. If no LLM key is set, you still
       get the retrieved passages to read yourself.

Why it's built this way:
    - Retrieval reuses the SAME local embedding model as clause alignment
      (docdiff/align.py, all-MiniLM-L6-v2) — no new heavy dependency, and the
      documents never leave the machine for the *search* step.
    - If the model can't load, retrieval falls back to a classic keyword score
      (TF-IDF cosine) implemented in pure Python — so the knowledge base always
      works, even with no numpy and no model.
    - Generation goes through the same AIProvider abstraction as everything else
      (Local / Ollama / Claude / Gemini), via provider.ask(). The grounding rule
      ("answer only from the sources, cite them, never guess") lives in one place
      (ai_providers.RAG_SYSTEM), so the guarantee is identical across engines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    source: str          # the filename the passage came from
    chunk_id: int
    header: str = ""     # short doc context (source + first line) added when indexing

    @property
    def index_text(self) -> str:
        """The text used for search — the passage plus a small header carrying the
        document's identity (filename + vendor line). Prepending this to every
        chunk means a query like 'GreenVolt warranty' can match the warranty line
        even though the vendor name is written elsewhere in the file. The header is
        NOT shown to the user; the displayed snippet stays the clean passage."""
        return f"{self.header}\n{self.text}".strip() if self.header else self.text


@dataclass
class Retrieved:
    chunk: Chunk
    score: float         # 0..1 relevance to the question


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _doc_header(text: str, source: str) -> str:
    """A one-line context tag: the filename plus the document's first real line
    (usually the vendor greeting), so every chunk knows which document it's from."""
    first = ""
    for ln in (text or "").splitlines():
        if ln.strip():
            first = ln.strip()[:80]
            break
    return f"Source: {source}. {first}".strip()


def _chunk_text(text: str, source: str, start_id: int = 0,
                max_words: int = 60, overlap: int = 15,
                header: str = "") -> tuple[list[Chunk], int]:
    """Pack a document's non-empty lines into ~max_words passages with a small
    word overlap, so an answer that straddles two lines still lands in one chunk."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    chunks: list[Chunk] = []
    cid = start_id
    cur: list[str] = []
    cur_words = 0

    def flush():
        nonlocal cur, cur_words, cid
        if cur:
            chunks.append(Chunk("\n".join(cur), source, cid, header))
            cid += 1

    for ln in lines:
        w = len(ln.split())
        if cur and cur_words + w > max_words:
            flush()
            # Carry the tail of the previous chunk forward as overlap.
            keep: list[str] = []
            kw = 0
            for prev in reversed(cur):
                keep.insert(0, prev)
                kw += len(prev.split())
                if kw >= overlap:
                    break
            cur = keep
            cur_words = sum(len(x.split()) for x in cur)
        cur.append(ln)
        cur_words += w
    flush()
    return chunks, cid


class KnowledgeBase:
    """An in-memory index of procurement passages you can search by meaning.

    Add documents, call build() once, then query(). Retrieval uses the local
    embedding model when available and falls back to TF-IDF keyword scoring.
    """

    def __init__(self):
        self.chunks: list[Chunk] = []
        self.used_model: bool = False   # True if meaning-based embeddings are active
        self._embeddings = None         # numpy (N x d), only when used_model
        self._idf: dict = {}            # lexical fallback: term -> idf weight
        self._chunk_vecs: list = []     # lexical fallback: [(tfidf dict, norm)]
        self._built: bool = False

    # --- building the index ---------------------------------------------------
    def add_document(self, file_bytes: bytes, filename: str) -> tuple[int, str]:
        """Read a file (reusing the app's extractor) and index its passages.
        Returns (passages_added, extractor_note)."""
        from .extract import extract
        ex = extract(file_bytes, filename)
        header = _doc_header(ex.text, filename)
        new, _ = _chunk_text(ex.text, filename, start_id=len(self.chunks),
                             header=header)
        self.chunks.extend(new)
        self._built = False
        return len(new), ex.note

    def add_text(self, text: str, source: str) -> int:
        header = _doc_header(text, source)
        new, _ = _chunk_text(text, source, start_id=len(self.chunks), header=header)
        self.chunks.extend(new)
        self._built = False
        return len(new)

    def build(self) -> None:
        """Compute the search index. Tries meaning-based embeddings; always builds
        the lexical index too as a fallback."""
        if not self.chunks:
            self._built = True
            return
        texts = [c.index_text for c in self.chunks]

        model = None
        try:
            from .align import _load_model
            model = _load_model()
        except Exception:
            model = None

        self._embeddings = None
        self.used_model = False
        if model is not None:
            try:
                from .align import _embed
                self._embeddings = _embed(model, texts)   # normalized (N x d)
                self.used_model = True
            except Exception:
                self._embeddings = None
                self.used_model = False

        self._build_lexical(texts)
        self._built = True

    def _build_lexical(self, texts: list[str]) -> None:
        import math
        toks = [_tokenize(t) for t in texts]
        df: dict = {}
        for tks in toks:
            for term in set(tks):
                df[term] = df.get(term, 0) + 1
        n = len(texts)
        # Smoothed idf so a term in every doc still carries a little weight.
        self._idf = {t: math.log((n + 1) / (d + 1)) + 1.0 for t, d in df.items()}
        self._chunk_vecs = [self._tfidf_vec(tks) for tks in toks]

    def _tfidf_vec(self, tokens: list[str]) -> tuple[dict, float]:
        import math
        tf: dict = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        vec = {t: c * self._idf.get(t, 0.0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return vec, norm

    # --- searching ------------------------------------------------------------
    def query(self, question: str, k: int = 5) -> list[Retrieved]:
        if not self.chunks:
            return []
        if not self._built:
            self.build()
        if self.used_model and self._embeddings is not None:
            return self._query_embeddings(question, k)
        return self._query_lexical(question, k)

    def _query_embeddings(self, question: str, k: int) -> list[Retrieved]:
        try:
            import numpy as np
            from .align import _load_model, _embed
            model = _load_model()
            if model is None:
                return self._query_lexical(question, k)
            q = _embed(model, [question])[0]
            # Vectors are normalized, so a dot product is cosine similarity in [-1,1].
            sims = self._embeddings @ q
            order = np.argsort(-sims)[:k]
            # Squash to a friendly 0..1 relevance for display.
            return [Retrieved(self.chunks[int(i)], float((sims[int(i)] + 1.0) / 2.0))
                    for i in order]
        except Exception:
            return self._query_lexical(question, k)

    def _query_lexical(self, question: str, k: int) -> list[Retrieved]:
        qvec, qnorm = self._tfidf_vec(_tokenize(question))
        scored: list[tuple[float, int]] = []
        for i, (vec, norm) in enumerate(self._chunk_vecs):
            # Iterate the smaller dict for the dot product.
            small, big = (qvec, vec) if len(qvec) <= len(vec) else (vec, qvec)
            dot = sum(w * big.get(t, 0.0) for t, w in small.items())
            score = dot / (qnorm * norm) if dot else 0.0
            scored.append((score, i))
        scored.sort(reverse=True)
        return [Retrieved(self.chunks[i], float(s)) for s, i in scored[:k]]

    # --- helpers --------------------------------------------------------------
    def is_empty(self) -> bool:
        return not self.chunks

    def doc_names(self) -> list[str]:
        return sorted({c.source for c in self.chunks})

    def save(self, path: str) -> None:
        """Persist the whole index to disk (local use — the free cloud host has no
        permanent disk). Pickle handles both the chunks and the numpy embeddings."""
        import pickle
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: str) -> "KnowledgeBase":
        import pickle
        with open(path, "rb") as f:
            return pickle.load(f)


def answer_question(kb: KnowledgeBase, question: str, provider, k: int = 5) -> dict:
    """Retrieve the top-k passages and let the provider write a grounded, cited
    answer. Falls back to showing the raw passages when no LLM is active."""
    hits = kb.query(question, k=k)
    if not hits:
        return {"answer": "The knowledge base is empty — add documents first.",
                "sources": [], "used_llm": False, "retrieval": "none"}

    context = "\n\n".join(
        f"[{i}] (from {h.chunk.source})\n{h.chunk.text}"
        for i, h in enumerate(hits, 1)
    )
    answer = provider.ask(question, context) if provider is not None else ""
    used_llm = bool(answer)
    if not used_llm:
        answer = ("_No LLM engine is active, so I can't synthesize an answer. "
                  "Here are the most relevant passages I found — read them "
                  "directly (they're ranked most-relevant first):_")

    sources = [{"label": i, "source": h.chunk.source,
                "snippet": h.chunk.text, "score": round(h.score, 3)}
               for i, h in enumerate(hits, 1)]
    return {
        "answer": answer,
        "sources": sources,
        "used_llm": used_llm,
        "retrieval": "meaning-based embeddings" if kb.used_model
                     else "keyword (TF-IDF)",
    }
