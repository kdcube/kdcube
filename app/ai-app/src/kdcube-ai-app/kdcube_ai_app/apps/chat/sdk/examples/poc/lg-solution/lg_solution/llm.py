"""LLM + embedding access, with an offline stub fallback.

`LLMClient` is the single seam between our graph and the model provider. It
exposes:

  - `embed(texts)`            -> list of vectors (real or deterministic stub)
  - `chat(system, user)`      -> a single string completion (non-streamed)
  - `chat_model()`            -> the raw LangChain Runnable, or None offline

The raw LangChain model is what the answer node streams from, so token-level
events surface through `graph.astream_events(...)` in the CLI.
"""
from __future__ import annotations

import hashlib
import math
from typing import List, Optional

from .config import Config


class LLMClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._chat_model = None  # lazily constructed LangChain model
        self._embeddings = None

    # -- embeddings ---------------------------------------------------------

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self.config.offline:
            return [self._stub_embed(t) for t in texts]
        emb = self._get_embeddings()
        return emb.embed_documents(list(texts))

    def _stub_embed(self, text: str) -> List[float]:
        """Deterministic pseudo-embedding: seed a tiny PRNG from a hash of the
        text and emit a unit vector of the configured width. Same text -> same
        vector, so cosine search over the DB behaves sensibly without a key."""
        dim = self.config.embed_dim
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        state = seed or 1
        vec: List[float] = []
        for _ in range(dim):
            # xorshift64* — cheap, dependency-free, deterministic
            state ^= (state << 13) & 0xFFFFFFFFFFFFFFFF
            state ^= state >> 7
            state ^= (state << 17) & 0xFFFFFFFFFFFFFFFF
            vec.append(((state / 0xFFFFFFFFFFFFFFFF) * 2.0) - 1.0)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _get_embeddings(self):
        if self._embeddings is None:
            from langchain_openai import OpenAIEmbeddings  # lazy

            self._embeddings = OpenAIEmbeddings(
                model=self.config.embed_model,
                api_key=self.config.openai_api_key,
            )
        return self._embeddings

    # -- chat ---------------------------------------------------------------

    def chat_model(self):
        """Raw streaming LangChain model, or None in offline mode."""
        if self.config.offline:
            return None
        if self._chat_model is None:
            from langchain_openai import ChatOpenAI  # lazy

            self._chat_model = ChatOpenAI(
                model=self.config.chat_model,
                api_key=self.config.openai_api_key,
                temperature=0.2,
                streaming=True,
            )
        return self._chat_model

    async def chat(self, system: str, user: str) -> str:
        """Non-streamed completion used by planning/synthesis helpers."""
        if self.config.offline:
            return self._stub_chat(system, user)
        from langchain_core.messages import HumanMessage, SystemMessage  # lazy

        model = self.chat_model()
        resp = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        return resp.content if isinstance(resp.content, str) else str(resp.content)

    def _stub_chat(self, system: str, user: str) -> str:
        head = user.strip().splitlines()[0] if user.strip() else ""
        return (
            "[offline stub] No API key set, so this is a canned response.\n"
            f"System role: {system[:80]}...\n"
            f"I would answer based on: {head[:200]}"
        )


def get_llm(config: Optional[Config] = None) -> LLMClient:
    from .config import get_config

    return LLMClient(config or get_config())
