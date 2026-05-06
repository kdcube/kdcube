# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from kdcube_ai_app.auth.sessions import RequestContext, UserSession
from kdcube_ai_app.apps.chat.ingress.chat_core import (
    IngressConfig,
    IngressResult,
    RawAttachment,
    map_gateway_error,
    process_chat_message,
    run_gateway_checks,
)
from kdcube_ai_app.apps.chat.ingress.signed_links import (
    SignedLink,
    SignedLinkToken,
    SignedLinkTokenError,
    SignedLinkTokenExpired,
    SignedLinkTokenInvalid,
    append_signed_link_token,
    make_signed_link,
    make_signed_link_token,
    verify_signed_link_token,
)

logger = logging.getLogger(__name__)


__all__ = [
    "ChatIngressSubmitter",
    "SignedLink",
    "SignedLinkToken",
    "SignedLinkTokenError",
    "SignedLinkTokenExpired",
    "SignedLinkTokenInvalid",
    "append_signed_link_token",
    "make_signed_link",
    "make_signed_link_token",
    "verify_signed_link_token",
]


@dataclass
class ChatIngressSubmitter:
    """
    Proc-local adapter for non-SSE/non-socket transports.

    It reuses the canonical shared chat ingestion core while obtaining queue,
    relay, conversation, Redis, and store resources from the current FastAPI app
    state. This is intended for proc-hosted bundle APIs such as public webhooks.
    """

    app: Any

    async def submit(
        self,
        *,
        session: UserSession,
        request_context: RequestContext,
        message_data: Dict[str, Any],
        message_text: str,
        ingress: IngressConfig,
        raw_attachments: Optional[List[RawAttachment]] = None,
        run_gateway_checks_first: bool = True,
    ) -> IngressResult:
        state = getattr(self.app, "state", None)
        missing = [
            name
            for name in (
                "chat_queue_manager",
                "chat_comm",
                "conversation_browser",
                "conversation_store",
                "redis_async",
            )
            if getattr(state, name, None) is None
        ]
        if missing:
            err = f"Chat submitter is unavailable; missing app.state resources: {', '.join(missing)}"
            logger.error(err)
            return IngressResult(
                ok=False,
                error_type="submitter_unavailable",
                error=err,
                http_status=503,
            )

        if run_gateway_checks_first:
            gateway_adapter = getattr(state, "gateway_adapter", None)
            if gateway_adapter is not None:
                gw_res = await run_gateway_checks(
                    gateway_adapter=gateway_adapter,
                    session=session,
                    context=request_context,
                    endpoint=ingress.entrypoint,
                )
                if gw_res.kind != "ok":
                    mapped = map_gateway_error(gw_res)
                    return IngressResult(
                        ok=False,
                        error_type=mapped.get("error_type") or "gateway_error",
                        error=mapped.get("message") or "System check failed",
                        http_status=int(mapped.get("status") or 503),
                        retry_after=mapped.get("retry_after"),
                    )
            elif os.getenv("CHAT_SUBMITTER_REQUIRE_GATEWAY", "0").lower() in {"1", "true", "yes", "on"}:
                return IngressResult(
                    ok=False,
                    error_type="gateway_unavailable",
                    error="Gateway adapter is unavailable",
                    http_status=503,
                )

        return await process_chat_message(
            app=self.app,
            chat_queue_manager=state.chat_queue_manager,
            chat_comm=state.chat_comm,
            session=session,
            request_context=request_context,
            message_data=message_data,
            message_text=message_text,
            ingress=ingress,
            raw_attachments=raw_attachments,
        )
