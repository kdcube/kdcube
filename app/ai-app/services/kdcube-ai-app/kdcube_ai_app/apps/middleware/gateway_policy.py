# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# middleware/gateway_policy.py
from __future__ import annotations
from dataclasses import dataclass
import re
from enum import Enum
from typing import Optional, Iterable, List

from fastapi import Request

from kdcube_ai_app.auth.AuthManager import RequirementBase

class EndpointClass(str, Enum):
    CONNECT = "connect"         # long-lived channel setup, no work
    READ = "read"               # read-only APIs
    CHAT_INGRESS = "chat_ingress"  # creates work (enqueue)

@dataclass(frozen=True)
class GatewayPolicy:
    cls: EndpointClass
    bypass_throttling: bool
    bypass_gate: bool
    requirements: Optional[List[RequirementBase]] = None

class GatewayPolicyResolver:
    """
    Central policy:
      - middleware uses this to decide how to resolve session
      - transport/ingress uses run_gateway_checks for CHAT_INGRESS
    """

    DEFAULT_GUARDED_REST_PATTERNS = (
        r"^/resources/link-preview$",
        r"^/resources/by-rn$",
        r"^/conversations/[^/]+/[^/]+/[^/]+/fetch$",
        r"^/conversations/[^/]+/[^/]+/turns-with-feedbacks$",
        r"^/conversations/[^/]+/[^/]+/feedback/conversations-in-period$",
        r"^/integrations/bundles/[^/]+/[^/]+/operations/[^/]+$",
    )

    def __init__(self, guarded_rest_patterns: Optional[Iterable[str]] = None):
        # REST paths that should be gated (rate limit + backpressure).
        # Keep this list small and focused on expensive endpoints.
        self._guarded_rest_patterns = tuple(
            re.compile(p) for p in (guarded_rest_patterns or self.DEFAULT_GUARDED_REST_PATTERNS)
            if isinstance(p, str) and p
        )

    def set_guarded_patterns(self, patterns: Iterable[str]) -> None:
        compiled = tuple(
            re.compile(p) for p in (patterns or self.DEFAULT_GUARDED_REST_PATTERNS)
            if isinstance(p, str) and p
        )
        self._guarded_rest_patterns = compiled

    def classify(self, path: str) -> EndpointClass:
        if path.startswith("/sse/stream"):
            return EndpointClass.CONNECT
        if path.startswith("/sse/chat"):
            return EndpointClass.CHAT_INGRESS
        if path.startswith("/socket.io"):
            # connect + events share prefix; let Socket handler decide
            return EndpointClass.CONNECT
        for pattern in self._guarded_rest_patterns:
            if pattern.match(path):
                return EndpointClass.CHAT_INGRESS
        return EndpointClass.READ

    def resolve(self, request: Request) -> GatewayPolicy:
        path = request.url.path
        cls = self.classify(path)

        # chat ingress for SSE/Socket.IO is gated inside transport handlers
        if cls == EndpointClass.CHAT_INGRESS and path.startswith(("/sse/", "/socket.io")):
            return GatewayPolicy(
                cls=cls,
                bypass_throttling=True,
                bypass_gate=True,
                requirements=[],
            )

        # REST ingress that should be gated
        if cls == EndpointClass.CHAT_INGRESS:
            return GatewayPolicy(
                cls=cls,
                bypass_throttling=False,
                bypass_gate=False,
                requirements=[],
            )

        # default: session resolution only; no counters
        if cls in (EndpointClass.CONNECT, EndpointClass.READ):
            return GatewayPolicy(
                cls=cls,
                bypass_throttling=True,
                bypass_gate=True,
                requirements=[],
            )

        # fallback
        return GatewayPolicy(
            cls=EndpointClass.READ,
            bypass_throttling=True,
            bypass_gate=True,
            requirements=[],
        )
