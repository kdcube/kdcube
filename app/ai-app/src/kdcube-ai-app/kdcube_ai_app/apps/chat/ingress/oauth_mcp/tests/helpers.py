# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Test helpers for descriptor-gated OAuth/MCP routes."""
from __future__ import annotations

from fastapi import FastAPI


def enable_oauth_mcp(app: FastAPI, *, issuer: str = "https://yey.boats") -> None:
    app.state.oauth_mcp_config = {
        "enabled": True,
        "issuer": issuer,
    }
