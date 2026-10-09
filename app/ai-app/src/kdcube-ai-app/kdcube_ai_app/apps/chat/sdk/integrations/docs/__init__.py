# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Provider-neutral document named-service integration.

Exports resolve lazily so provider adapters can import the neutral
``selectors`` and ``tables`` modules without loading the named service.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "DOCS_IMPORT_SOURCE_KIND",
    "DOCS_NAMESPACE",
    "DocsNamedServiceProvider",
    "document_export_ref",
    "document_source_ref",
    "docs_named_service_spec",
    "make_docs_named_service_provider",
    "parse_docs_export_ref",
    "parse_docs_ref",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        module = import_module(
            "kdcube_ai_app.apps.chat.sdk.integrations.docs.named_service"
        )
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
