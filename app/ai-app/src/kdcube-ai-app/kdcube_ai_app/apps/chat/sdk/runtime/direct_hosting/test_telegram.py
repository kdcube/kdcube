from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.channels import (
    DirectTurnResult,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.telegram import (
    DirectTelegramConfig,
    DirectTelegramCredentials,
    DirectTelegramRequestError,
    DirectTelegramWebhook,
    TELEGRAM_WEBHOOK_SECRET_HEADER,
    configured_direct_telegram,
    create_direct_telegram_app,
    resolve_direct_telegram_credentials,
)


def _update(update_id: int, *, text: str = "hello") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 10,
            "chat": {"id": 700, "type": "private"},
            "from": {"id": 900, "username": "alice"},
            "text": text,
        },
    }


def _result(turn_id: str = "turn-1") -> DirectTurnResult:
    return DirectTurnResult(
        answer="completed",
        turn_id=turn_id,
        turn_log={"turn_id": turn_id, "blocks": []},
    )


def test_telegram_configuration_is_descriptor_owned() -> None:
    resolved = configured_direct_telegram(
        {
            "agent": {
                "ingress": {
                    "telegram": {
                        "host": "127.0.0.1",
                        "port": 8787,
                        "path": "/telegram/webhook",
                        "bot_token_ref": "platform.services.telegram.bot_token",
                        "webhook_secret_ref": (
                            "platform.services.telegram.webhook_secret"
                        ),
                    }
                }
            }
        }
    )

    assert resolved == DirectTelegramConfig(
        host="127.0.0.1",
        port=8787,
        path="/telegram/webhook",
        bot_token_ref="platform.services.telegram.bot_token",
        webhook_secret_ref="platform.services.telegram.webhook_secret",
    )


def test_telegram_configuration_rejects_unqualified_secret_refs() -> None:
    with pytest.raises(ValueError, match="platform-qualified"):
        configured_direct_telegram(
            {
                "agent": {
                    "ingress": {
                        "telegram": {
                            "bot_token_ref": "telegram.bot_token",
                            "webhook_secret_ref": (
                                "platform.services.telegram.webhook_secret"
                            ),
                        }
                    }
                }
            }
        )


def test_telegram_configuration_rejects_non_loopback_binding() -> None:
    with pytest.raises(ValueError, match="local mode"):
        configured_direct_telegram(
            {
                "agent": {
                    "ingress": {
                        "telegram": {
                            "host": "0.0.0.0",
                            "bot_token_ref": ("platform.services.telegram.bot_token"),
                            "webhook_secret_ref": (
                                "platform.services.telegram.webhook_secret"
                            ),
                        }
                    }
                }
            }
        )


@pytest.mark.asyncio
async def test_telegram_credentials_resolve_from_secret_refs() -> None:
    values = {
        "platform.services.telegram.bot_token": "bot-token",
        "platform.services.telegram.webhook_secret": "webhook-secret",
    }

    async def read_secret(ref: str) -> str:
        return values[ref]

    credentials = await resolve_direct_telegram_credentials(
        DirectTelegramConfig(
            host="127.0.0.1",
            port=8787,
            path="/telegram/webhook",
            bot_token_ref="platform.services.telegram.bot_token",
            webhook_secret_ref="platform.services.telegram.webhook_secret",
        ),
        secret_reader=read_secret,
    )

    assert credentials == DirectTelegramCredentials(
        bot_token="bot-token",
        webhook_secret="webhook-secret",
    )


@pytest.mark.asyncio
async def test_webhook_rejects_a_missing_or_invalid_secret() -> None:
    webhook = DirectTelegramWebhook(
        credentials=DirectTelegramCredentials("bot-token", "expected"),
        run_turn=AsyncMock(return_value=_result()),
    )

    with pytest.raises(DirectTelegramRequestError, match="secret_missing"):
        await webhook.process(provided_secret="", update=_update(1))
    with pytest.raises(DirectTelegramRequestError, match="secret_invalid"):
        await webhook.process(provided_secret="wrong", update=_update(1))


@pytest.mark.asyncio
async def test_fastapi_route_injects_the_request_body() -> None:
    webhook = DirectTelegramWebhook(
        credentials=DirectTelegramCredentials("bot-token", "expected"),
        run_turn=AsyncMock(return_value=_result()),
        deliver=AsyncMock(return_value={"telegram_delivery": {"ok": True}}),
    )
    app = create_direct_telegram_app(
        config=DirectTelegramConfig(
            host="127.0.0.1",
            port=8787,
            path="/telegram/webhook",
            bot_token_ref="platform.services.telegram.bot_token",
            webhook_secret_ref="platform.services.telegram.webhook_secret",
        ),
        webhook=webhook,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://direct-agent.test",
    ) as client:
        response = await client.post(
            "/telegram/webhook",
            headers={TELEGRAM_WEBHOOK_SECRET_HEADER: "expected"},
            json=_update(2),
        )

    assert response.status_code == 200
    assert response.json()["stage"] == "completed-inline"


@pytest.mark.asyncio
async def test_webhook_maps_identity_hydrates_files_and_delivers_durable_turn() -> None:
    runner = AsyncMock(return_value=_result())
    hydrate = AsyncMock(
        return_value=[
            {
                "filename": "input.png",
                "mime": "image/png",
                "base64": base64.b64encode(b"png-bytes").decode("ascii"),
            }
        ]
    )
    deliver = AsyncMock(return_value={"telegram_delivery": {"ok": True}})
    webhook = DirectTelegramWebhook(
        credentials=DirectTelegramCredentials("bot-token", "expected"),
        run_turn=runner,
        hydrate=hydrate,
        deliver=deliver,
    )
    update = _update(4, text="inspect this")
    update["message"]["document"] = {
        "file_id": "telegram-file",
        "file_name": "input.png",
        "mime_type": "image/png",
    }

    response = await webhook.process(provided_secret="expected", update=update)

    assert response["stage"] == "completed-inline"
    request = runner.await_args.args[0]
    assert request.user_id == "telegram_900"
    assert request.user_type == "external"
    assert request.session_id == "telegram_chat_700"
    assert request.conversation_id == "telegram_chat_700"
    assert request.source == "telegram-local"
    assert request.source_id == "4"
    assert request.attachments[0].filename == "input.png"
    assert request.attachments[0].content == b"png-bytes"
    assert deliver.await_args.kwargs["chat_id"] == 700
    assert deliver.await_args.kwargs["turn_result"]["turn_log"]["turn_id"] == ("turn-1")


@pytest.mark.asyncio
async def test_webhook_deduplicates_updates_in_one_process() -> None:
    runner = AsyncMock(return_value=_result())
    webhook = DirectTelegramWebhook(
        credentials=DirectTelegramCredentials("bot-token", "expected"),
        run_turn=runner,
        deliver=AsyncMock(return_value={"telegram_delivery": {"ok": True}}),
    )

    first = await webhook.process(provided_secret="expected", update=_update(8))
    second = await webhook.process(provided_secret="expected", update=_update(8))

    assert first["stage"] == "completed-inline"
    assert second["stage"] == "duplicate-update"
    runner.assert_awaited_once()


@pytest.mark.asyncio
async def test_attachment_hydration_failure_is_actionable_and_retryable() -> None:
    runner = AsyncMock(return_value=_result())
    hydrate = AsyncMock(
        side_effect=[
            [{"file_id": "telegram-file", "error": "download failed"}],
            [
                {
                    "filename": "input.png",
                    "mime": "image/png",
                    "base64": base64.b64encode(b"png-bytes").decode("ascii"),
                }
            ],
        ]
    )
    webhook = DirectTelegramWebhook(
        credentials=DirectTelegramCredentials("bot-token", "expected"),
        run_turn=runner,
        hydrate=hydrate,
        deliver=AsyncMock(return_value={"telegram_delivery": {"ok": True}}),
    )
    update = _update(9, text="inspect this")
    update["message"]["document"] = {"file_id": "telegram-file"}

    with pytest.raises(
        DirectTelegramRequestError, match="telegram_attachment_hydration_failed"
    ):
        await webhook.process(provided_secret="expected", update=update)
    response = await webhook.process(provided_secret="expected", update=update)

    assert response["stage"] == "completed-inline"
    runner.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_serializes_turns_without_claiming_arrival_order() -> None:
    active = 0
    maximum_active = 0

    async def run_turn(_request):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return _result()

    webhook = DirectTelegramWebhook(
        credentials=DirectTelegramCredentials("bot-token", "expected"),
        run_turn=run_turn,
        deliver=AsyncMock(return_value={"telegram_delivery": {"ok": True}}),
    )

    await asyncio.gather(
        webhook.process(provided_secret="expected", update=_update(10)),
        webhook.process(provided_secret="expected", update=_update(11)),
    )

    assert maximum_active == 1


@pytest.mark.asyncio
async def test_failed_update_can_be_retried() -> None:
    runner = AsyncMock(side_effect=[RuntimeError("failed"), _result()])
    webhook = DirectTelegramWebhook(
        credentials=DirectTelegramCredentials("bot-token", "expected"),
        run_turn=runner,
        deliver=AsyncMock(return_value={"telegram_delivery": {"ok": True}}),
    )

    with pytest.raises(RuntimeError, match="failed"):
        await webhook.process(provided_secret="expected", update=_update(12))
    response = await webhook.process(provided_secret="expected", update=_update(12))

    assert response["stage"] == "completed-inline"
    assert runner.await_count == 2
