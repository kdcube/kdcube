# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# Event filter for the OpenRouter data-processing bundle.
# Controls which SSE events are forwarded to the client.


class BundleEventFilter:
    """Simple pass-through filter — forward all events to the client."""

    def filter(self, event: dict) -> bool:
        return True
