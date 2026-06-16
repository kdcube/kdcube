# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""faiss vector backends for the generic index (optional faiss + numpy deps)."""
from .store import LocalFaissStore, CachedFaissStore

__all__ = ["LocalFaissStore", "CachedFaissStore"]
