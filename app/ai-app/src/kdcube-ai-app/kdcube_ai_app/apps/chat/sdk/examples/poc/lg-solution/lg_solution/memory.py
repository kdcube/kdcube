"""Per-user semantic memory over pgvector.

A small facts/preferences store: `remember(user_id, text)` writes a note,
`recall(user_id, query, k)` returns the k most relevant notes for that user by
cosine similarity. Scoped by user_id so the CLI's `--user` flag gives each
person their own memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from ._pg import connect, to_vector_literal
from .config import Config
from .llm import LLMClient


@dataclass
class MemoryHit:
    text: str
    score: float  # cosine similarity in [0, 1]


class SemanticMemory:
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
                CREATE TABLE IF NOT EXISTS memories (
                    id         BIGSERIAL PRIMARY KEY,
                    user_id    TEXT NOT NULL,
                    text       TEXT NOT NULL,
                    embedding  vector({self.config.embed_dim}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS memories_user_idx ON memories (user_id)")

    def remember(self, user_id: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        vec = self.llm.embed([text])[0]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memories (user_id, text, embedding) VALUES (%s, %s, %s::vector)",
                (user_id, text, to_vector_literal(vec)),
            )

    def recall(self, user_id: str, query: str, k: int = 5) -> List[MemoryHit]:
        query = (query or "").strip()
        if not query:
            return []
        vec = self.llm.embed([query])[0]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT text, 1 - (embedding <=> %s::vector) AS score
                FROM memories
                WHERE user_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (to_vector_literal(vec), user_id, to_vector_literal(vec), k),
            )
            return [MemoryHit(text=row[0], score=float(row[1])) for row in cur.fetchall()]
