"""LLM + embedding access, with an offline stub fallback.

`LLMClient` is the single seam between our graph and the model provider. It
exposes:

  - `await embed(texts)`      -> list of vectors (real or deterministic stub)
  - `chat(system, user)`      -> a single string completion (non-streamed)
  - `chat_model()`            -> the raw LangChain Runnable, or None offline

The raw LangChain model is what the answer node streams from, so token-level
events surface through `graph.astream_events(...)` in the CLI.

When hosted by KDCube, an optional `models_service` handle routes both chat and
embeddings through the platform's model service (auto-accounted, still streaming)
via the reusable framework adapters, without changing any graph/node logic. With
no `models_service` the standalone paths (`ChatOpenAI` / `OpenAIEmbeddings`, or
the offline stub) are used exactly as before.

For economic ENFORCEMENT (T2b), the host may additionally pass an
`embedding_service` — the entrypoint's economics-guarded search facade — so
retrieval/memory embeddings are budget-checked per call (and degrade to the raw
accounted path when economics is off). Chat generation stays on the raw
`models_service`; its spend is enforced at the turn level by the economics base
entrypoint's budget preflight around the whole turn.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, List, Optional

from .config import Config


class LLMClient:
    def __init__(
        self,
        config: Config,
        models_service: Any = None,
        model_role: Optional[str] = None,
        embedding_service: Any = None,
        summary_model_role: Optional[str] = None,
    ) -> None:
        self.config = config
        self.models_service = models_service
        self.model_role = model_role
        # A DISTINCT accounted role for the compaction summary call, so it bills
        # separately from the answer and (running in the `compact` node) never
        # streams as the answer. None -> no summary model (compaction falls back to
        # trim). Only meaningful when a models_service is present (hosted).
        self.summary_model_role = summary_model_role
        self._summary_chat_model = None
        # Optional economics-guarded embedding facade (T2b). Either a ready
        # object exposing async `embed_texts`, OR a zero-arg provider returning
        # one. A provider is re-invoked per embed so the guard's economics
        # subject tracks the CURRENT turn's identity — the graph (and this
        # client) is built once and reused across turns/users, so a frozen
        # build-time bind would leak the first turn's subject.
        self.embedding_service = embedding_service
        self._chat_model = None  # lazily constructed LangChain model
        self._embeddings = None

    # -- embeddings ---------------------------------------------------------

    def _resolve_embedding_service(self) -> Any:
        """Resolve the guarded embedding facade for THIS call.

        Returns the facade (or the raw model service the facade degrades to when
        economics is disabled), or None when no facade was wired. A provider
        callable is invoked fresh each time so the economics subject follows the
        current turn.
        """
        svc = self.embedding_service
        if svc is None:
            return None
        try:
            return svc() if callable(svc) else svc
        except Exception:
            return None

    async def embed(self, texts: List[str]) -> List[List[float]]:
        # Async so embedding runs on the event loop inside a graph node, within
        # the turn's bound accounting context — no sync-over-async bridge, so
        # @track_embedding bills this turn and the processor loop never blocks.
        # T2b: when the host wired an economics-guarded facade, retrieval/memory
        # embeddings route through it (per-call budget preflight/settlement); it
        # degrades to the raw accounted service when economics is off. Offline
        # stub only when there is no embedder AND no models_service AND no API
        # key: a hosted service embeds (and accounts) even without a local key.
        guarded = self._resolve_embedding_service()
        if guarded is None and self.models_service is None and self.config.offline:
            return [self._stub_embed(t) for t in texts]
        emb = self._get_embeddings(guarded)
        # Route to the ASYNC embedding path: KDCubeEmbeddings.aembed_documents
        # awaits the embedder's embed_texts directly (accounted, no bridge);
        # OpenAIEmbeddings provides its own async implementation.
        return await emb.aembed_documents(list(texts))

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

    def _get_embeddings(self, guarded: Any = None):
        # T2b: a resolved economics-guarded facade wins. It is NOT cached — the
        # facade is rebuilt per call so its economics subject tracks the current
        # turn (the client is shared across turns/users).
        if guarded is not None:
            from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import KDCubeEmbeddings

            return KDCubeEmbeddings(guarded)
        if self._embeddings is None:
            if self.models_service is not None:
                # Route embeddings through KDCube's accounted embedding path.
                from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import KDCubeEmbeddings

                self._embeddings = KDCubeEmbeddings(self.models_service)
            else:
                from langchain_openai import OpenAIEmbeddings  # lazy

                self._embeddings = OpenAIEmbeddings(
                    model=self.config.embed_model,
                    api_key=self.config.openai_api_key,
                )
        return self._embeddings

    # -- chat ---------------------------------------------------------------

    def chat_model(self):
        """Raw streaming LangChain model, or None in offline mode."""
        if self._chat_model is None:
            if self.models_service is not None:
                # Stream through KDCube's accounted model service; the graph's
                # answer node keeps calling `.astream(...)`, so on_chat_model_stream
                # events still surface through astream_events.
                from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import KDCubeChatModel

                self._chat_model = KDCubeChatModel(
                    models_service=self.models_service,
                    role=self.model_role,
                    temperature=0.2,
                    # A generous output cap — the adapter's small default silently
                    # amputates long answers mid-generation.
                    max_tokens=int(getattr(self.config, "max_tokens", 0) or 8192),
                )
            elif self.config.offline:
                return None
            else:
                from langchain_openai import ChatOpenAI  # lazy

                self._chat_model = ChatOpenAI(
                    model=self.config.chat_model,
                    api_key=self.config.openai_api_key,
                    temperature=0.2,
                    streaming=True,
                )
        return self._chat_model

    def summary_chat_model(self):
        """Raw streaming LangChain model on the DISTINCT summary role, or None.

        Hosted (a models_service is present) -> an accounted KDCubeChatModel bound
        to ``summary_model_role``; the compaction node uses it to fold older turns
        into a running summary, billed apart from the answer. None with no service
        (offline) -> the compaction node degrades to trim."""
        if self.models_service is None or not self.summary_model_role:
            return None
        if self._summary_chat_model is None:
            from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import KDCubeChatModel

            self._summary_chat_model = KDCubeChatModel(
                models_service=self.models_service,
                role=self.summary_model_role,
                temperature=0.2,
            )
        return self._summary_chat_model

    async def chat(self, system: str, user: str) -> str:
        """Non-streamed completion used by planning/synthesis helpers."""
        # Offline stub only when there is no models_service AND no API key.
        if self.models_service is None and self.config.offline:
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


def get_llm(
    config: Optional[Config] = None,
    models_service: Any = None,
    model_role: Optional[str] = None,
    embedding_service: Any = None,
    summary_model_role: Optional[str] = None,
) -> LLMClient:
    from .config import get_config

    return LLMClient(
        config or get_config(),
        models_service=models_service,
        model_role=model_role,
        embedding_service=embedding_service,
        summary_model_role=summary_model_role,
    )
