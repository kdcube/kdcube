from __future__ import annotations

import traceback
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.comm.sink import (
    STATS_COMM_EVENT_SELECTOR,
    StatsTelemetrySink,
    StatsTelemetryTarget,
    configure_stats_event_recording,
)


async def make_event_sink(
    entrypoint: Any,
    *,
    bundle_id: str,
    token_secret: str,
    get_secret_fn: Any,
) -> StatsTelemetrySink | None:
    endpoint_url = str(entrypoint.bundle_prop("telemetry_sink.endpoint_url", "") or "").strip()
    if not endpoint_url:
        return None
    token = str(await get_secret_fn(token_secret, bundle_id=bundle_id) or "").strip()
    if not token:
        try:
            entrypoint.logger.log(
                f"[{bundle_id}] telemetry sink endpoint is configured but secret "
                f"{token_secret} is missing; event sending disabled.",
                "WARNING",
            )
        except Exception:
            pass
        return None
    auth_header = str(entrypoint.bundle_prop("telemetry_sink.auth_header", "Authorization") or "").strip() or "Authorization"
    return StatsTelemetrySink(
        StatsTelemetryTarget(
            endpoint_url=endpoint_url,
            token=token,
            token_header=auth_header,
        ),
        source_bundle=bundle_id,
    )


async def configure_event_recording(
    entrypoint: Any,
    *,
    bundle_id: str,
    event_record_max: int,
) -> None:
    try:
        comm = entrypoint.comm
        sink = await entrypoint._make_event_sink()
        telemetry_enabled = entrypoint._telemetry_events_enabled()
        if sink is None or not telemetry_enabled:
            comm.stop_recording()
            comm.set_event_sink(None)
            comm.clear_recorded_events(STATS_COMM_EVENT_SELECTOR)
            return
        selector = entrypoint._build_telemetry_selector()
        configure_stats_event_recording(
            comm,
            sink,
            selector=selector,
            scope={"owner": "react", "bundle": bundle_id, "runtime": "on_message"},
            max_events=event_record_max,
        )
    except Exception:
        entrypoint.logger.log(traceback.format_exc(), "WARNING")


async def send_recorded_events(entrypoint: Any) -> Dict[str, Any]:
    try:
        selector = entrypoint._build_telemetry_selector()
        return await entrypoint.comm.send_recorded_events(selector)
    except Exception:
        entrypoint.logger.log(traceback.format_exc(), "WARNING")
        return {"ok": False, "error": "Unable to flush recorded versatile events."}
