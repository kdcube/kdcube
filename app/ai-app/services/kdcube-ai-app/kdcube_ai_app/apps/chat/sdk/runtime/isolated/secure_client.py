# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/isolated/secure_client.py

import asyncio
import json
import base64
from typing import Any, Dict


class ToolStub:
    """Client for calling tools via the privileged supervisor socket."""

    def __init__(self, socket_path: str = '/tmp/supervisor.sock'):
        self.socket_path = socket_path

    @staticmethod
    def _encode_params(params: dict) -> dict:
        """Recursively encode bytes values as base64 with special marker."""
        if not isinstance(params, dict):
            return params

        result = {}
        for key, value in params.items():
            if isinstance(value, bytes):
                result[key] = {
                    "__type__": "bytes",
                    "__data__": base64.b64encode(value).decode("ascii")
                }
            elif isinstance(value, dict):
                result[key] = ToolStub._encode_params(value)
            elif isinstance(value, list):
                result[key] = [
                    ToolStub._encode_params(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    async def call_tool(self, tool_id: str, params: dict, reason: str | None = None) -> dict:
        """
        Call the privileged supervisor for a tool execution.
        Now ASYNC to properly integrate with the executor's event loop.
        """
        # Encode any bytes parameters
        encoded_params = self._encode_params(params or {})

        payload = {
            "tool_id": tool_id,
            "params": encoded_params,
        }
        if reason:
            payload["reason"] = reason

        try:
            # Use asyncio Unix socket connection
            reader, writer = await asyncio.open_unix_connection(self.socket_path)

            # Send request
            request = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            writer.write(request)
            await writer.drain()

            # Signal we're done sending
            writer.write_eof()

            # Read response until EOF
            response_bytes = await reader.read()

            writer.close()
            await writer.wait_closed()

            if not response_bytes:
                return {"ok": False, "error": "Empty response from supervisor"}

            return json.loads(response_bytes.decode("utf-8"))

        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"Invalid JSON from supervisor: {e}"}
        except Exception as e:
            return {"ok": False, "error": f"Stub connection failed: {e}"}