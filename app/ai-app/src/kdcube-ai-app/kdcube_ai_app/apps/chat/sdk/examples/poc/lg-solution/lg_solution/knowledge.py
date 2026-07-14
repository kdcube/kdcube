"""A pgvector knowledge base.

`ingest(docs)` upserts documents (dedup by title), `search(query, k)` returns the
top-k by cosine similarity. `seed()` loads a handful of sample docs so a fresh
run can answer something immediately.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from ._pg import connect, to_vector_literal
from .config import Config
from .llm import LLMClient


@dataclass
class Doc:
    title: str
    text: str


@dataclass
class KBHit:
    title: str
    text: str
    score: float


# A tiny domain KB so the assistant is useful out of the box.
SEED_DOCS: List[Doc] = [
    Doc(
        "LangGraph checkpointers",
        "LangGraph persists graph state via checkpointers keyed by thread_id. "
        "The Postgres checkpointer stores each step so a conversation can resume "
        "across process restarts. Swap MemorySaver for PostgresSaver to persist.",
    ),
    Doc(
        "pgvector basics",
        "pgvector adds a `vector` column type to Postgres and distance operators. "
        "`<=>` is cosine distance; ORDER BY embedding <=> query gives nearest "
        "neighbours. Create the extension with CREATE EXTENSION vector.",
    ),
    Doc(
        "Retrieval-augmented generation",
        "RAG retrieves relevant documents for a query and feeds them to the model "
        "as grounding context, reducing hallucination and letting answers cite a "
        "source corpus rather than only model parameters.",
    ),
    Doc(
        "astream_events streaming",
        "graph.astream_events(version='v2') yields typed events: on_chain_start, "
        "on_chat_model_stream (token chunks), on_chain_end. A UI or CLI subscribes "
        "to these to render tokens and node progress as they happen.",
    ),
    Doc(
        "Subagents as sub-graphs",
        "A subagent can be a nested StateGraph the main graph delegates a scoped "
        "sub-question to. It runs its own retrieve/synthesize steps and returns a "
        "compact result, keeping the parent graph's context focused.",
    ),
]


class KnowledgeBase:
    def __init__(self, config: Config, llm: LLMClient) -> None:
        self.config = config
        self.llm = llm
        self._ready = False

    def _conn(self):
        conn = connect(self.config.database_url)
        if not self._ready:
            self._ensure_schema(conn)
            self._ready = True
        return conn

    def _ensure_schema(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS kb_documents (
                    id        BIGSERIAL PRIMARY KEY,
                    title     TEXT UNIQUE NOT NULL,
                    text      TEXT NOT NULL,
                    embedding vector({self.config.embed_dim}) NOT NULL
                )
                """
            )

    def ingest(self, docs: Sequence[Doc]) -> int:
        docs = [d for d in docs if d.text.strip()]
        if not docs:
            return 0
        vecs = self.llm.embed([f"{d.title}\n{d.text}" for d in docs])
        with self._conn() as conn, conn.cursor() as cur:
            for d, v in zip(docs, vecs):
                cur.execute(
                    """
                    INSERT INTO kb_documents (title, text, embedding)
                    VALUES (%s, %s, %s::vector)
                    ON CONFLICT (title) DO UPDATE
                        SET text = EXCLUDED.text, embedding = EXCLUDED.embedding
                    """,
                    (d.title, d.text, to_vector_literal(v)),
                )
        return len(docs)

    def seed(self) -> int:
        """Idempotent: only ingests when the table is empty."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM kb_documents")
            if cur.fetchone()[0] > 0:
                return 0
        return self.ingest(SEED_DOCS)

    def search(self, query: str, k: int = 4) -> List[KBHit]:
        query = (query or "").strip()
        if not query:
            return []
        vec = self.llm.embed([query])[0]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, text, 1 - (embedding <=> %s::vector) AS score
                FROM kb_documents
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (to_vector_literal(vec), to_vector_literal(vec), k),
            )
            return [KBHit(title=r[0], text=r[1], score=float(r[2])) for r in cur.fetchall()]
