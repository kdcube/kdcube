# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Compatibility surface for the hybrid index, which now lives in the foundation.

The implementation moved to `app_foundation.index.sqlite` because both the
platform and host-side tools need it and neither owns it. This module re-exports
the same objects so existing platform imports keep working unchanged.

These are re-exports, not subclasses or wrappers: the classes here are the same
objects as the foundation's, so isinstance checks and identity comparisons hold
across the boundary. Nothing is redefined below, on purpose. A second definition
would drift from the first the moment either changed, which is the exact problem
the move exists to end.
"""
from app_foundation.index.sqlite import (
    Document,
    EmbedFn,
    FusionWeights,
    HybridIndex,
    IndexConfig,
    SearchHit,
    VectorStore,
)
from app_foundation.index.vector_store import BruteForceVectorStore

__all__ = [
    "HybridIndex",
    "Document",
    "SearchHit",
    "IndexConfig",
    "FusionWeights",
    "VectorStore",
    "EmbedFn",
    "BruteForceVectorStore",
]
