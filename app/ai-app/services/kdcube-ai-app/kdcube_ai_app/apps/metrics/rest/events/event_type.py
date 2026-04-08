from typing import Literal, Any
from pydantic import BaseModel
from datetime import datetime

class EventBase(BaseModel):
    event_type : Literal["log", "metric"]
    origin: str  # Denotes the origin of the event (e.g., 'kdcube-frontend')

    tenant: str
    project: str
    user_id: str | None
    session_id: str | None
    conversation_id: str | None
    timestamp: datetime
    timezone: str


class ExternalLogEvent(EventBase):
    level:   Literal["error", "warn", "info"]
    message: str
    args:    list[Any]