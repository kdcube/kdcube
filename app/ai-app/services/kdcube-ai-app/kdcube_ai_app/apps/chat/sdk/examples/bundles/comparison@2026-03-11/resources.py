# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# ── resources.py ──
# User-facing error messages for the comparison bundle.

import random
from typing import Dict, List

from kdcube_ai_app.infra.service_hub.errors import ServiceError

SUPPORT_ADDRESS = "info@kdcube.tech"

FRIENDLY_ERROR_MESSAGES: Dict[str, List[str]] = {
    "usage_limit": [
        f"I'm temporarily unable to process comparison requests due to high usage. "
        f"Please try again in a few minutes or contact {SUPPORT_ADDRESS}.",
    ],
    "rate_limit": [
        "You're making comparison requests faster than I can handle. "
        "Please wait a moment and try again.",
    ],
    "server_error": [
        f"Something went wrong while generating the comparison. "
        f"Please try again or contact {SUPPORT_ADDRESS}.",
    ],
    "timeout": [
        "The comparison is taking longer than expected. Please try again.",
    ],
}


def get_friendly_error_message(error_code: str, fallback: bool = True) -> str:
    messages = FRIENDLY_ERROR_MESSAGES.get(error_code)
    if messages:
        return random.choice(messages)
    if fallback:
        return f"A temporary issue occurred. Please try again or contact {SUPPORT_ADDRESS}."
    raise ValueError(f"Unknown error code: {error_code}")


def handle_service_error(error: ServiceError) -> str:
    error_code_map = {
        "usage_limit": "usage_limit",
        "rate_limit": "rate_limit",
        "quota_exceeded": "usage_limit",
        "too_many_requests": "rate_limit",
        "timeout": "timeout",
        "internal_error": "server_error",
        "server_error": "server_error",
    }
    message_code = error_code_map.get(error.code, "server_error")
    return get_friendly_error_message(message_code)
