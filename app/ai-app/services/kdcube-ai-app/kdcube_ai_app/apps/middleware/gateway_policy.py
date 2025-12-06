# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# middleware/gateway_policy.py
from __future__ import annotations
from dataclasses import dataclass
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

    def __init__(self):
        # keep config here if needed later
        pass

    def classify(self, path: str) -> EndpointClass:
        if path.startswith("/sse/stream"):
            return EndpointClass.CONNECT
        if path.startswith("/sse/chat"):
            return EndpointClass.CHAT_INGRESS
        if path.startswith("/socket.io"):
            # connect + events share prefix; let Socket handler decide
            return EndpointClass.CONNECT
        return EndpointClass.READ

    def resolve(self, request: Request) -> GatewayPolicy:
        cls = self.classify(request.url.path)

        # default: session resolution only; no counters
        if cls in (EndpointClass.CONNECT, EndpointClass.READ, EndpointClass.CHAT_INGRESS):
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
