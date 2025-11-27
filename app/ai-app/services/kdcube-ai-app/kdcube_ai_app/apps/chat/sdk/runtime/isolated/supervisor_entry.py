# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/isolated/supervisor_entry.py


import socket
import struct
import os
import json
import asyncio
from typing import Dict, Any, Callable, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools


class PrivilegedSupervisor:
    def __init__(self, socket_path: str = "/tmp/supervisor.sock", logger: Optional[AgentLogger] = None):
        self.socket_path = socket_path
        self.allowed_child_pid: Optional[int] = None
        self.registered_tools: dict[str, Callable[..., Any]] = {}
        self.log = logger or AgentLogger("priv_supervisor")

        # Remove old socket
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        # Create Unix domain socket
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.socket_path)

        # Only supervisor user can access this socket (root inside container)
        os.chmod(self.socket_path, 0o600)
        self.sock.listen(8)

    def set_allowed_child(self, pid: int):
        """Only this specific PID can make requests."""
        self.allowed_child_pid = pid

    def set_allowed_tools(self, tool_ids: list[str]):
        """Register which tool_ids are allowed (string ids only)."""
        # in a more advanced version you can map tool_id -> callable here
        self.registered_tools = {tid: None for tid in tool_ids}

    def register_tool_callables(self, tool_map: Dict[str, Callable[..., Any]]):
        """
        Optional: provide direct mapping tool_id -> callable
        if you don't want to rely on importlib resolution.
        """
        self.registered_tools.update(tool_map or {})

    def _resolve_tool_fn(self, tool_id: str) -> Optional[Callable[..., Any]]:
        fn = self.registered_tools.get(tool_id)
        if fn is not None:
            return fn

        # Fallback: resolve from "<alias>.<fn_name>"
        # This still uses alias embedded in tool_id, but we do NOT pass alias separately.
        try:
            alias, name = tool_id.split(".", 1)
        except ValueError:
            return None

        try:
            mod = __import__(alias)
            owner = getattr(mod, "tools", mod)
            fn = getattr(owner, name, None)
            if callable(fn):
                self.registered_tools[tool_id] = fn
                return fn
        except Exception:
            return None

        return None

    def handle_request(self):
        """
        Blocking handler for a single request.
        Intended to be called from an executor / separate task.
        """
        conn, _ = self.sock.accept()

        try:
            # Get peer credentials (Linux-specific)
            creds = conn.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize('3i')
            )
            pid, uid, gid = struct.unpack('3i', creds)

            # Enforce pid & uid (uid=1001 child, pid=allowed_child_pid)
            if self.allowed_child_pid is not None and pid != self.allowed_child_pid:
                conn.sendall(b'{"ok": false, "error": "Unauthorized process"}')
                return

            if uid != 1001:  # your unprivileged uid inside container
                conn.sendall(b'{"ok": false, "error": "Wrong UID"}"')
                return

            data = conn.recv(4096)
            if not data:
                conn.sendall(b'{"ok": false, "error": "Empty request"}')
                return

            request = json.loads(data.decode("utf-8"))
            result = self.execute_privileged_operation(request)
            conn.sendall(json.dumps(result).encode("utf-8"))
        except Exception as e:
            try:
                conn.sendall(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    def execute_privileged_operation(self, request: dict) -> dict:
        """Only this code can access network/credentials.

        IMPORTANT:
        This MUST go through io_tools.tool_call so that:
          - tool_calls_index.json is updated
          - <sanitized>-<idx>.json is written
          - all side effects we care about are preserved.

        In *this* context, io_tools.tool_call executes the tool directly
        (no supervisor recursion), which is exactly what we want.
        """
        tool_id = request.get("tool_id")
        params = request.get("params") or {}
        reason = request.get("reason") or ""

        if not tool_id:
            return {"ok": False, "error": "Missing tool_id"}

        if self.registered_tools and tool_id not in self.registered_tools:
            return {"ok": False, "error": f"Unknown tool: {tool_id}"}

        fn = self._resolve_tool_fn(tool_id)
        if fn is None:
            return {"ok": False, "error": f"Could not resolve tool callable for {tool_id}"}

        try:
            async def _run():
                return await agent_io_tools.tool_call(
                    fn=fn,
                    params_json=json.dumps(params, ensure_ascii=False),
                    call_reason=reason,
                    tool_id=tool_id,
                )

            # Supervisor side is sync, so we drive the async tool_call here.
            out = asyncio.run(_run())

            # tool_call already persisted the call; we just forward the raw result
            return {"ok": True, "result": out}
        except Exception as e:
            self.log.log(f"[supervisor] Tool {tool_id} failed: {e}", level="ERROR")
            return {"ok": False, "error": str(e)}

    async def handle_stream(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """
        Async version of handle_request for asyncio.start_unix_server.
        Runs in the supervisor's event loop → shares ContextVars with everything else.
        """
        try:
            sock = writer.get_extra_info("socket")
            if sock is None:
                writer.write(b'{"ok": false, "error": "No underlying socket"}')
                await writer.drain()
                return

            # SO_PEERCRED: pid, uid, gid
            creds = sock.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize('3i')
            )
            pid, uid, gid = struct.unpack('3i', creds)

            # We relaxed PID, so just enforce UID (your unprivileged UID, e.g. 1001)
            if uid != 1001:
                writer.write(b'{"ok": false, "error": "Wrong UID"}')
                await writer.drain()
                return

            # Read request (you can make this loop if you want streaming)
            data = await reader.read(4096)
            if not data:
                writer.write(b'{"ok": false, "error": "Empty request"}')
                await writer.drain()
                return

            request = json.loads(data.decode("utf-8"))
            result = self.execute_privileged_operation(request)
            writer.write(json.dumps(result).encode("utf-8"))
            await writer.drain()

        except Exception as e:
            try:
                writer.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass