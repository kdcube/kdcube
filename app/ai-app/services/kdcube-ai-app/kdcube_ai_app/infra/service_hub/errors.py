# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/service_hub/errors.py

from enum import Enum
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field
import asyncio

from kdcube_ai_app.infra.accounting.usage import (
    ClientConfigHint,
)

class ServiceKind(str, Enum):
    llm = "llm"
    embedding = "embedding"
    custom = "custom"
    other = "other"


class ServiceError(BaseModel):
    """
    Canonical error object for any backend service (LLM, embeddings, KB, etc).
    This is what you propagate up to agents / API layer.
    """
    kind: ServiceKind = Field(..., description="Service type: llm, embedding, ...")

    # Where the error came from
    service_name: str = Field(
        ...,
        description="Logical service name (e.g. 'ModelServiceBase', 'gate_stream', 'kb_client')"
    )
    provider: Optional[str] = Field(
        None,
        description="Provider identifier (e.g. 'openai', 'anthropic', 'custom')."
    )
    model_name: Optional[str] = Field(
        None,
        description="Model name or endpoint identifier, when applicable."
    )

    # What happened
    error_type: str = Field(
        ...,
        description="Short classifier, usually Exception.__class__.__name__ or a domain code."
    )
    message: str = Field(
        ...,
        description="Human-readable error message, safe to log/return."
    )
    stage: Optional[str] = Field(
        None,
        description="Phase in which it happened (e.g. 'stream_loop', 'parse', 'format_fix', 'http_request')."
    )
    http_status: Optional[int] = Field(
        None,
        description="HTTP status code, for HTTP-backed services."
    )
    code: Optional[str] = Field(
        None,
        description="Provider-specific code, e.g. 'rate_limit', 'timeout', 'invalid_request'."
    )

    # How to treat it
    retryable: Optional[bool] = Field(
        None,
        description="Whether a retry might succeed (best-effort guess)."
    )

    # Extra context (do not put secrets here)
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form extra data (request id, role, operation, etc)."
    )

class ServiceException(Exception):
    """
    Raise this to bubble a ServiceError through normal exception handling.
    """
    def __init__(self, err: ServiceError, *, cause: Exception | None = None):
        super().__init__(err.message)
        self.err = err
        if cause is not None:
            self.__cause__ = cause

def mk_llm_error(
        exc: Exception,
        stage: str,
        cfg: ClientConfigHint,
        service_name: str,
        http_status: int | None = None,
        code: str | None = None,
        retryable: bool | None = None,
        context: dict | None = None,
) -> ServiceError:
    return ServiceError(
        kind=ServiceKind.llm,
        service_name=service_name,
        provider=getattr(cfg, "provider", None),
        model_name=getattr(cfg, "model_name", None),
        error_type=type(exc).__name__,
        message=str(exc),
        stage=stage,
        http_status=http_status,
        code=code,
        retryable=retryable,
        context=context or {},
    )
