# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/reg.py

import re
from collections import defaultdict

MODEL_CONFIGS = {
    "o3-mini": {
        "model_name": "o3-mini",
        "provider": "openai",
        "has_classifier": True,
        "temperature": False,
        "tools": False,
        "description": "GPT-o3 Mini"
    },
    "gpt-4.1-nano": {
        "model_name": "gpt-4.1-nano-2025-04-14",
        "provider": "openai",
        "has_classifier": True,
        "description": "GPT-4.1 Nano"
    },
    "gpt-4o": {
        "model_name": "gpt-4o",
        "provider": "openai",
        "has_classifier": True,
        "description": "GPT-4 Optimized - OpenAI model"
    },
    "gpt-4o-mini": {
        "model_name": "gpt-4o-mini",
        "provider": "openai",
        "has_classifier": True,
        "description": "GPT-4 Optimized Mini - High performance, cost-effective"
    },
    "claude-3-7-sonnet-latest": {
        "model_name": "claude-3-7-sonnet-20250219",
        "provider": "anthropic",
        "has_classifier": True,
        "description": "Claude Sonnet 3.7"
    },
    "claude-sonnet-4-20250514": {
        "model_name": "claude-sonnet-4-20250514",
        "provider": "anthropic",
        "has_classifier": True,
        "description": "Claude Sonnet 4 Sonnet - Latest Anthropic Model"
    },
    "claude-3-5-haiku-latest": {
        "model_name": "claude-3-5-haiku-20241022",
        "provider": "anthropic",
        "has_classifier": False,
        "description": "Claude 3 Haiku - Fast and efficient"
    },
    "claude-3-7-sonnet-20250219": {
        "model_name": "claude-3-7-sonnet-20250219",
        "provider": "anthropic",
        "has_classifier": False,
        "description": "Claude 3.7 Sonnett"
    }
}
EMBEDDERS = {
    # OpenAI Embeddings
    "openai-text-embedding-3-small": {
        "provider": "openai",
        "model_name": "text-embedding-3-small",
        "dim": 1536,
        "description": "OpenAI Text Embedding 3 Small - High performance, cost-effective"
    },
    "openai-text-embedding-3-large": {
        "provider": "openai",
        "model_name": "text-embedding-3-large",
        "dim": 3072,
        "description": "OpenAI Text Embedding 3 Large - Highest quality"
    },
    "openai-text-embedding-ada-002": {
        "provider": "openai",
        "model_name": "text-embedding-ada-002",
        "dim": 1536,
        "description": "OpenAI Ada 002 - Previous generation"
    },

    # Custom/Sentence Transformer Embeddings
    "sentence-transformers/all-MiniLM-L6-v2": {
        "provider": "custom",
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "dim": 384,
        "description": "All MiniLM L6 v2 - Lightweight and fast"
    },
    "sentence-transformers/distiluse-base-multilingual-cased": {
        "provider": "custom",
        "model_name": "sentence-transformers/distiluse-base-multilingual-cased",
        "dim": 512,
        "description": "DistilUSE Multilingual - Good for multilingual content"
    },
    "sentence-transformers/all-mpnet-base-v2": {
        "provider": "custom",
        "model_name": "sentence-transformers/all-mpnet-base-v2",
        "dim": 768,
        "description": "All MPNet Base v2 - High quality general purpose"
    }
}


_SEEDED_FALSE = [
    (re.compile(r"^o3", re.I), {"temperature": False, "top_p": False, "tools": False}),  # o3, o3-mini, etc.
    (re.compile(r"^o4", re.I), {"temperature": False, "top_p": False, "tools": False, "reasoning": True}),  # o4-* reasoning models
    (re.compile(r"^gpt-5", re.I), {"temperature": False, "top_p": False, "tools": True, "reasoning": True}),  # gpt-5-* reasoning models
    (re.compile(r"^gpt-4o", re.I), {"temperature": True, "top_p": False, "tools": True}),
]


# Sparse learned overrides: only set keys we actually learned.
_dynamic_caps: dict[str, dict[str, bool]] = {}

def model_caps(model: str) -> dict:
    caps = {"temperature": True, "top_p": True}

    # apply seeded knowledge
    for rx, preset in _SEEDED_FALSE:
        if rx.match(model):
            caps.update(preset)
            break

    # apply learned overrides only if present
    learned = _dynamic_caps.get(model)
    if learned:
        caps.update(learned)

    return caps

def learn_unsupported(model: str, param: str):
    _dynamic_caps.setdefault(model, {})[param] = False

def learn_supported(model: str, param: str):
    _dynamic_caps.setdefault(model, {})[param] = True
