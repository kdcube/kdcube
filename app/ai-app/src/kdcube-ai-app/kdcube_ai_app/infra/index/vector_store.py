# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Compatibility surface for the vector-store contract, now in the foundation.

The protocol and the dependency-free store moved to
`app_foundation.index.vector_store`. faiss backends stay here in
`kdcube_ai_app.infra.index.faiss`, because they carry faiss and numpy and a
foundation package should not.

Re-exports, not redefinitions: a faiss store still satisfies the same protocol
object the foundation defines.
"""
from app_foundation.index.vector_store import BruteForceVectorStore, VectorStore

__all__ = ["VectorStore", "BruteForceVectorStore"]
