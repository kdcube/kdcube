# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/secure_client.py

import socket
import json

class ToolStub:
    """This module is imported by generated code

    Even though this is open source and the attacker can read it,
    they CANNOT replicate it because:
    1. They don't run in the correct UID (1001)
    2. They don't have the correct PID
    3. The socket is only accessible to the supervisor's child
    """

    def __init__(self, socket_path: str = '/tmp/supervisor.sock'):
        self.socket_path = socket_path

    def call_tool(self, tool_id: str, params: dict, reason: str | None = None) -> dict:
        """Call the privileged supervisor for a tool execution.

        We always talk in terms of tool_id, never alias.
        but the supervisor will reject the connection because:
        - Wrong PID (kernel enforced via SO_PEERCRED)
        - Wrong UID (process runs as different user)

        _call_supervisor('web_search', {'query': query...})
        """
        payload = {
            "tool_id": tool_id,
            "params": params or {},
        }
        if reason:
            payload["reason"] = reason

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)

            request = json.dumps(payload)
            sock.sendall(request.encode("utf-8"))

            # naive single-read; can be extended to loop if needed
            response = sock.recv(4096)
            sock.close()

            return json.loads(response.decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": str(e)}
