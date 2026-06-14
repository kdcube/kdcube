# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/utils/cors.py

import re

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from kdcube_ai_app.apps.chat.sdk.config import get_settings


def _glob_origin_to_regex(origin: str) -> str | None:
    value = str(origin or "").strip().rstrip("/")
    if not value or value == "*" or "*" not in value:
        return None
    return "^" + re.escape(value).replace(r"\*", r"[^/]*") + "$"


def _cors_origin_options(cors_config) -> tuple[list[str], str | None]:
    exact_origins: list[str] = []
    regex_parts: list[str] = []

    configured_regex = str(getattr(cors_config, "allow_origin_regex", "") or "").strip()
    if configured_regex:
        regex_parts.append(f"(?:{configured_regex})")

    for origin in getattr(cors_config, "allow_origins", []) or []:
        value = str(origin or "").strip().rstrip("/")
        if not value:
            continue
        if value == "*":
            exact_origins.append("*")
            continue
        regex = _glob_origin_to_regex(value)
        if regex:
            regex_parts.append(f"(?:{regex})")
        else:
            exact_origins.append(value)

    return exact_origins, "|".join(regex_parts) if regex_parts else None


def configure_cors(app: FastAPI):
    settings = get_settings()
    cors_config = settings.CORS_CONFIG_OBJ
    allow_origins = None
    if cors_config:
        allow_origins, allow_origin_regex = _cors_origin_options(cors_config)
        allow_credentials = cors_config.allow_credentials
        allow_headers = cors_config.allow_headers
        allow_methods = cors_config.allow_methods

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_origin_regex=allow_origin_regex,
            allow_credentials=bool(allow_credentials),
            allow_methods=allow_methods,
            allow_headers=allow_headers,
        )
    return allow_origins
