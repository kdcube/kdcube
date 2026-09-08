"""Local-only Telegram webhook adapter for directly hosted agents."""

from __future__ import annotations

import asyncio
import hmac
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    deliver_turn_to_telegram,
    hydrate_telegram_attachments,
    raw_attachments_from_telegram,
    summarize_telegram_update,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.channels import (
    DirectInputAttachment,
    DirectTurnRequest,
    DirectTurnRunner,
)


TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
DIRECT_TELEGRAM_UPDATE_WINDOW = 2048


@dataclass(frozen=True)
class DirectTelegramConfig:
    """Descriptor-owned endpoint and secret references for local Telegram mode."""

    host: str
    port: int
    path: str
    bot_token_ref: str
    webhook_secret_ref: str


@dataclass(frozen=True)
class DirectTelegramCredentials:
    bot_token: str
    webhook_secret: str


class DirectTelegramRequestError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def configured_direct_telegram(config: Mapping[str, Any]) -> DirectTelegramConfig:
    """Resolve ``agent.ingress.telegram`` without reading secret values."""
    agent = config.get("agent")
    if not isinstance(agent, Mapping):
        raise ValueError("configuration section 'agent' must be a mapping")
    ingress = agent.get("ingress")
    if not isinstance(ingress, Mapping):
        raise ValueError("agent.ingress must be a mapping")
    telegram = ingress.get("telegram")
    if not isinstance(telegram, Mapping):
        raise ValueError("agent.ingress.telegram must be a mapping")

    host = str(telegram.get("host") or "127.0.0.1").strip()
    path = str(telegram.get("path") or "/telegram/webhook").strip()
    bot_token_ref = str(telegram.get("bot_token_ref") or "").strip()
    webhook_secret_ref = str(telegram.get("webhook_secret_ref") or "").strip()
    try:
        port = int(telegram.get("port") or 8787)
    except (TypeError, ValueError) as exc:
        raise ValueError("agent.ingress.telegram.port must be an integer") from exc
    if not host:
        raise ValueError("agent.ingress.telegram.host is required")
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError(
            "agent.ingress.telegram.host must be 127.0.0.1 or localhost in local mode"
        )
    if not 1 <= port <= 65535:
        raise ValueError("agent.ingress.telegram.port must be between 1 and 65535")
    if not path.startswith("/") or "?" in path or "#" in path:
        raise ValueError("agent.ingress.telegram.path must be an absolute URL path")
    for name, value in (
        ("bot_token_ref", bot_token_ref),
        ("webhook_secret_ref", webhook_secret_ref),
    ):
        if not value.startswith("platform."):
            raise ValueError(
                f"agent.ingress.telegram.{name} must be a platform-qualified secret ref"
            )
    return DirectTelegramConfig(
        host=host,
        port=port,
        path=path,
        bot_token_ref=bot_token_ref,
        webhook_secret_ref=webhook_secret_ref,
    )


async def resolve_direct_telegram_credentials(
    config: DirectTelegramConfig,
    *,
    secret_reader: Callable[[str], Awaitable[str | None]] = get_secret,
) -> DirectTelegramCredentials:
    """Resolve Telegram credentials on the trusted side before serving."""
    bot_token, webhook_secret = await asyncio.gather(
        secret_reader(config.bot_token_ref),
        secret_reader(config.webhook_secret_ref),
    )
    if not str(bot_token or "").strip():
        raise ValueError(f"Telegram bot token is missing at {config.bot_token_ref}")
    if not str(webhook_secret or "").strip():
        raise ValueError(
            f"Telegram webhook secret is missing at {config.webhook_secret_ref}"
        )
    return DirectTelegramCredentials(
        bot_token=str(bot_token).strip(),
        webhook_secret=str(webhook_secret).strip(),
    )


def _telegram_prompt(summary: Mapping[str, Any]) -> str:
    text = str(summary.get("text") or "").strip()
    if text:
        return text
    if summary.get("attachments"):
        return (
            "The user sent Telegram attachment(s) without text. Inspect the "
            "attachment(s) and respond to what is present."
        )
    return ""


def _direct_request(
    *,
    summary: Mapping[str, Any],
    attachments: tuple[DirectInputAttachment, ...],
) -> DirectTurnRequest:
    chat_id = str(summary.get("chat_id") or "").strip()
    telegram_user_id = str(summary.get("user_id") or chat_id).strip()
    if not chat_id:
        raise DirectTelegramRequestError(400, "telegram_chat_id_missing")
    if not telegram_user_id:
        raise DirectTelegramRequestError(400, "telegram_user_id_missing")
    return DirectTurnRequest(
        prompt=_telegram_prompt(summary),
        user_id=f"telegram_{telegram_user_id}",
        user_type="external",
        session_id=f"telegram_chat_{chat_id}",
        conversation_id=f"telegram_chat_{chat_id}",
        attachments=attachments,
        source="telegram-local",
        source_id=str(summary.get("update_id") or ""),
    )


class DirectTelegramWebhook:
    """Validated inline webhook execution for one local development process."""

    def __init__(
        self,
        *,
        credentials: DirectTelegramCredentials,
        run_turn: DirectTurnRunner,
        hydrate: Callable[
            ..., Awaitable[list[dict[str, Any]]]
        ] = hydrate_telegram_attachments,
        deliver: Callable[..., Awaitable[dict[str, Any]]] = deliver_turn_to_telegram,
    ) -> None:
        self.credentials = credentials
        self.run_turn = run_turn
        self._hydrate = hydrate
        self._deliver = deliver
        self._claim_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._claimed: set[str] = set()
        self._claim_order: deque[str] = deque()

    async def _claim(self, update_id: str) -> bool:
        async with self._claim_lock:
            if update_id in self._claimed:
                return False
            while len(self._claim_order) >= DIRECT_TELEGRAM_UPDATE_WINDOW:
                self._claimed.discard(self._claim_order.popleft())
            self._claimed.add(update_id)
            self._claim_order.append(update_id)
            return True

    async def _release(self, update_id: str) -> None:
        async with self._claim_lock:
            self._claimed.discard(update_id)
            try:
                self._claim_order.remove(update_id)
            except ValueError:
                pass

    async def process(
        self,
        *,
        provided_secret: str,
        update: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not provided_secret:
            raise DirectTelegramRequestError(401, "telegram_webhook_secret_missing")
        if not hmac.compare_digest(
            str(provided_secret), self.credentials.webhook_secret
        ):
            raise DirectTelegramRequestError(401, "telegram_webhook_secret_invalid")
        if not isinstance(update, Mapping):
            raise DirectTelegramRequestError(400, "telegram_update_must_be_an_object")

        summary = summarize_telegram_update(update)
        update_id = str(summary.get("update_id") or "").strip()
        if not update_id:
            raise DirectTelegramRequestError(400, "telegram_update_id_missing")
        if not await self._claim(update_id):
            return {
                "ok": True,
                "accepted": True,
                "stage": "duplicate-update",
                "update_id": update_id,
            }

        try:
            prompt = _telegram_prompt(summary)
            if not prompt:
                return {
                    "ok": True,
                    "accepted": True,
                    "stage": "empty-update",
                    "update_id": update_id,
                }
            hydrated = await self._hydrate(
                attachments=list(summary.get("attachments") or []),
                bot_token=self.credentials.bot_token,
                message_id=summary.get("message_id"),
            )
            if any(str(item.get("error") or "").strip() for item in hydrated):
                raise DirectTelegramRequestError(
                    502, "telegram_attachment_hydration_failed"
                )
            raw = raw_attachments_from_telegram(hydrated)
            if len(raw) != len(summary.get("attachments") or []):
                raise DirectTelegramRequestError(
                    422, "telegram_attachment_type_not_supported_in_local_mode"
                )
            attachments = tuple(
                DirectInputAttachment(
                    filename=item.name,
                    mime=item.mime,
                    content=item.content,
                )
                for item in raw
            )
            request = _direct_request(summary=summary, attachments=attachments)
            # Direct mode has no distributed turn queue. Serializing prevents
            # overlapping mutation, but does not promise webhook arrival order.
            async with self._turn_lock:
                result = await self.run_turn(request)
                delivery = await self._deliver(
                    bundle_id="direct-agent-local",
                    bot_token=self.credentials.bot_token,
                    chat_id=summary.get("chat_id"),
                    update_id=update_id,
                    turn_result=result.transport_payload(),
                )
            return {
                "ok": True,
                "accepted": True,
                "stage": "completed-inline",
                "update_id": update_id,
                "turn_id": result.turn_id,
                "delivery": delivery,
            }
        except Exception:
            await self._release(update_id)
            raise


def create_direct_telegram_app(
    *,
    config: DirectTelegramConfig,
    webhook: DirectTelegramWebhook,
) -> Any:
    """Create the local FastAPI endpoint without a KDCube app server."""
    from fastapi import FastAPI, HTTPException, Request

    app = FastAPI(title="KDCube direct-agent Telegram development hook")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "mode": "direct-telegram-local"}

    async def telegram_webhook(request: Request) -> dict[str, Any]:
        try:
            update = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="telegram_update_json_invalid"
            ) from exc
        try:
            return await webhook.process(
                provided_secret=str(
                    request.headers.get(TELEGRAM_WEBHOOK_SECRET_HEADER) or ""
                ),
                update=update,
            )
        except DirectTelegramRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    # Request is imported lazily so direct-hosting users without FastAPI can
    # still import the SDK module. Resolve the postponed annotation before
    # FastAPI inspects the route signature.
    telegram_webhook.__annotations__["request"] = Request
    app.post(config.path)(telegram_webhook)

    return app


async def serve_direct_telegram(
    *,
    config: DirectTelegramConfig,
    run_turn: DirectTurnRunner,
) -> None:
    """Run the explicit local-only inline Telegram webhook server."""
    import uvicorn

    credentials = await resolve_direct_telegram_credentials(config)
    webhook = DirectTelegramWebhook(credentials=credentials, run_turn=run_turn)
    app = create_direct_telegram_app(config=config, webhook=webhook)
    print("mode: local Telegram webhook; agent execution occurs inline")
    print(f"listen: http://{config.host}:{config.port}{config.path}")
    print("delivery guarantees: process-local development mode")
    server = uvicorn.Server(
        uvicorn.Config(app, host=config.host, port=config.port, log_level="info")
    )
    await server.serve()


__all__ = [
    "DirectTelegramConfig",
    "DirectTelegramCredentials",
    "DirectTelegramRequestError",
    "DirectTelegramWebhook",
    "DIRECT_TELEGRAM_UPDATE_WINDOW",
    "TELEGRAM_WEBHOOK_SECRET_HEADER",
    "configured_direct_telegram",
    "create_direct_telegram_app",
    "resolve_direct_telegram_credentials",
    "serve_direct_telegram",
]
