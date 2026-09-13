# SPDX-License-Identifier: MIT
"""The platform import path must stay usable after the index moved.

`kdcube_ai_app.infra.index.sqlite` is now a re-export of
`app_foundation.index.sqlite`. Existing platform consumers import from the old
path and must not notice.

Identity is the part worth testing rather than assuming. If these were
subclasses, wrappers or a second definition, isinstance checks would start
failing across the boundary and pickles written by one side would not load on
the other, in ways that appear far from here.
"""
from __future__ import annotations

import app_foundation.index.sqlite as foundation
import app_foundation.index.vector_store as foundation_vectors
import kdcube_ai_app.infra.index.sqlite as platform
import kdcube_ai_app.infra.index.vector_store as platform_vectors


def test_the_platform_path_exports_the_same_objects_not_copies():
    for name in (
        "HybridIndex",
        "Document",
        "SearchHit",
        "IndexConfig",
        "FusionWeights",
        "VectorStore",
        "EmbedFn",
    ):
        assert getattr(platform, name) is getattr(foundation, name), name


def test_the_vector_store_contract_is_one_object_on_both_paths():
    assert platform_vectors.VectorStore is foundation_vectors.VectorStore
    assert platform_vectors.BruteForceVectorStore is foundation_vectors.BruteForceVectorStore
    # The convenience re-export on the sqlite package is the same store again.
    assert platform.BruteForceVectorStore is foundation_vectors.BruteForceVectorStore


def test_classes_still_name_their_defining_module():
    """__module__ points at the foundation, which is where they now live.

    Recorded rather than asserted as a compatibility promise: anything that
    pickles these, or logs their module, sees the new name. Nothing in the
    platform does today, and this test is where that would be noticed.
    """

    assert platform.HybridIndex.__module__.startswith("app_foundation.")
    assert platform.Document.__module__.startswith("app_foundation.")


def test_the_platform_still_owns_the_faiss_backend():
    """The foundation deliberately does not carry faiss or numpy."""

    import kdcube_ai_app.infra.index.faiss as faiss_backend

    assert faiss_backend.__name__.startswith("kdcube_ai_app.")
