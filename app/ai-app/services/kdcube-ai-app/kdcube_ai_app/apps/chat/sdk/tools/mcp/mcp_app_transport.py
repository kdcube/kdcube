# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any


def run_stdio(app: Any) -> None:
    if hasattr(app, "run_stdio"):
        return app.run_stdio()
    if hasattr(app, "run"):
        return app.run()
    raise RuntimeError("FastMCP does not expose run_stdio/run")


def run_sse(app: Any, *, host: str, port: int) -> None:
    if hasattr(app, "run_sse"):
        return app.run_sse(host=host, port=port)
    if hasattr(app, "run"):
        return app.run(transport="sse", host=host, port=port)
    raise RuntimeError("FastMCP does not expose run_sse/run")


def run_http(app: Any, *, host: str, port: int) -> None:
    if hasattr(app, "run_http"):
        return app.run_http(host=host, port=port)
    if hasattr(app, "run"):
        return app.run(transport="streamable-http", host=host, port=port)
    raise RuntimeError("FastMCP does not expose run_http/run")
