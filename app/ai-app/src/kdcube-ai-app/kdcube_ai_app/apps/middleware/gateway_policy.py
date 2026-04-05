# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# middleware/gateway_policy.py
from __future__ import annotations
from dataclasses import dataclass
import re
from enum import Enum
from typing import Optional, Iterable, List

from fastapi import Request
import os

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
    bypass_backpressure: bool
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
        r"^/integrations/bundles/[^/]+/[^/]+/[^/]+$",
        r"^/integrations/bundles/[^/]+/[^/]+/[^/]+/widgets$",
        r"^/integrations/bundles/[^/]+/[^/]+/[^/]+/public/[^/]+$",
        r"^/integrations/bundles/[^/]+/[^/]+/[^/]+/widgets/[^/]+$",
        r"^/integrations/bundles/[^/]+/[^/]+/operations/[^/]+$",
        r"^/integrations/bundles/[^/]+/[^/]+/[^/]+/operations/[^/]+$",
        r"^/integrations/static/[^/]+/[^/]+/[^/]+$",
        r"^/integrations/static/[^/]+/[^/]+/[^/]+/.*$",
        r"^/api/integrations/bundles/[^/]+/[^/]+/[^/]+$",
        r"^/api/integrations/bundles/[^/]+/[^/]+/[^/]+/widgets$",
        r"^/api/integrations/bundles/[^/]+/[^/]+/[^/]+/public/[^/]+$",
        r"^/api/integrations/bundles/[^/]+/[^/]+/[^/]+/widgets/[^/]+$",
        r"^/api/integrations/bundles/[^/]+/[^/]+/operations/[^/]+$",
        r"^/api/integrations/bundles/[^/]+/[^/]+/[^/]+/operations/[^/]+$",
        r"^/api/integrations/static/[^/]+/[^/]+/[^/]+$",
        r"^/api/integrations/static/[^/]+/[^/]+/[^/]+/.*$",
    )

    def __init__(self, guarded_rest_patterns: Optional[Iterable[str]] = None):
        # REST paths that should be gated (rate limit + backpressure).
        # Keep this list small and focused on expensive endpoints.
        self._guarded_rest_patterns = tuple(
            re.compile(p) for p in (guarded_rest_patterns or self.DEFAULT_GUARDED_REST_PATTERNS)
            if isinstance(p, str) and p
        )
        self._component = (os.getenv("GATEWAY_COMPONENT") or "ingress").strip().lower()
        self._bypass_throttling_patterns: tuple[re.Pattern, ...] = tuple()

    def _path_candidates(self, path: str) -> tuple[str, ...]:
        """
        Return path variants for suffix matching.
        We match against the full path and against suffixes obtained by
        progressively stripping leading path segments.
        """
        if not path:
            return (path,)
        clean = path if path.startswith("/") else f"/{path}"
        segments = [seg for seg in clean.split("/") if seg]
        if not segments:
            return ("/",)
        candidates: list[str] = []
        # Full path first
        candidates.append(clean)
        # Then suffixes by dropping leading segments
        for i in range(1, len(segments)):
            candidates.append("/" + "/".join(segments[i:]))
        # de-dup while preserving order
        seen = set()
        unique: list[str] = []
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return tuple(unique)

    def set_guarded_patterns(self, patterns: Iterable[str]) -> None:
        compiled = tuple(
            re.compile(p) for p in (patterns or self.DEFAULT_GUARDED_REST_PATTERNS)
            if isinstance(p, str) and p
        )
        self._guarded_rest_patterns = compiled

    def set_bypass_throttling_patterns(self, patterns: Iterable[str]) -> None:
        compiled = tuple(
            re.compile(p) for p in (patterns or [])
            if isinstance(p, str) and p
        )
        self._bypass_throttling_patterns = compiled

    def classify(self, path: str) -> EndpointClass:
        if self._component == "proc":
            if path.startswith("/api/integrations/"):
                return EndpointClass.CHAT_INGRESS
            return EndpointClass.READ
        if path.startswith("/sse/stream"):
            return EndpointClass.CONNECT
        if path.startswith("/sse/chat"):
            return EndpointClass.CHAT_INGRESS
        if path.startswith("/socket.io"):
            # connect + events share prefix; let Socket handler decide
            return EndpointClass.CONNECT
        candidates = self._path_candidates(path)
        for pattern in self._guarded_rest_patterns:
            if any(pattern.match(candidate) for candidate in candidates):
                return EndpointClass.CHAT_INGRESS
        return EndpointClass.READ

    def resolve(self, request: Request) -> GatewayPolicy:
        path = request.url.path
        candidates = self._path_candidates(path)
        cls = self.classify(path)

        # chat ingress for SSE/Socket.IO is gated inside transport handlers
        if cls == EndpointClass.CHAT_INGRESS and path.startswith(("/sse/", "/socket.io")):
            return GatewayPolicy(
                cls=cls,
                bypass_throttling=True,
                bypass_gate=True,
                bypass_backpressure=True,
                requirements=[],
            )

        # REST ingress that should be gated
        if cls == EndpointClass.CHAT_INGRESS:
            return GatewayPolicy(
                cls=cls,
                bypass_throttling=False,
                bypass_gate=False,
                bypass_backpressure=False,
                requirements=[],
            )

        # default: session resolution only; no counters
        if cls in (EndpointClass.CONNECT, EndpointClass.READ):
            pol = GatewayPolicy(
                cls=cls,
                bypass_throttling=False,
                bypass_gate=False,
                bypass_backpressure=True,
                requirements=[],
            )
            if self._bypass_throttling_patterns and any(
                p.match(candidate)
                for p in self._bypass_throttling_patterns
                for candidate in candidates
            ):
                return GatewayPolicy(
                    cls=pol.cls,
                    bypass_throttling=True,
                    bypass_gate=pol.bypass_gate,
                    bypass_backpressure=pol.bypass_backpressure,
                    requirements=pol.requirements,
                )
            return pol

        # fallback
        return GatewayPolicy(
            cls=EndpointClass.READ,
            bypass_throttling=False,
            bypass_gate=False,
            bypass_backpressure=True,
            requirements=[],
        )
