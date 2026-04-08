# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# metrics/rest/events/events.py
import logging
from fastapi import APIRouter, HTTPException
from kdcube_ai_app.apps.metrics.rest.events.event_type import ExternalLogEvent
from kdcube_ai_app.apps.metrics.rest.events.service import LogCollectorService
import kdcube_ai_app.apps.utils.logging_config as logging_config

logging_config.configure_logging()
logger = logging.getLogger("Metrics.Events")

router = APIRouter()

# Initialize service
log_collector_service = LogCollectorService()


@router.post("/client")
async def receive_client_event(event: ExternalLogEvent):
    """Receive and process client-side log event"""
    try:
        log_collector_service.process(event)
        return {
            "status": "received",
            "event_type": event.event_type,
            "level": event.level,
        }
    except Exception as e:
        logger.error(f"Error processing client event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process event")