# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""KDCube <-> LangChain bridge: route a LangChain/LangGraph agent's model and
embedding calls through KDCube's model service (auto-accounted, still streaming)
without changing the agent's graph or logic.

    from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import (
        KDCubeChatModel, KDCubeEmbeddings,
    )
"""
from kdcube_ai_app.apps.chat.sdk.frameworks.langchain.chat_model import KDCubeChatModel
from kdcube_ai_app.apps.chat.sdk.frameworks.langchain.embeddings import KDCubeEmbeddings

__all__ = ["KDCubeChatModel", "KDCubeEmbeddings"]
