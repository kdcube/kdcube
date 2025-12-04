# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/isolated/supervisor_entry.py

import socket
import struct
import os, base64, json
import asyncio
from typing import Dict, Any, Callable, Optional

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools


class PrivilegedSupervisor:
    def __init__(self, socket_path: str = "/tmp/supervisor.sock", logger: Optional[AgentLogger] = None):
        self.socket_path = socket_path
        self.allowed_child_pid: Optional[int] = None
        self.registered_tools: dict[str, Callable[..., Any]] = {}
        self.log = logger or AgentLogger("supervisor")
        self.alias_to_dyn: Dict[str, str] = {}

        # Remove old socket
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        # Create Unix domain socket
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.socket_path)

        # Executor (UID 1001) needs to access this socket
        os.chmod(self.socket_path, 0o666)
        self.sock.listen(8)

    def set_alias_map(self, alias_map: Dict[str, str]):
        """Register alias→dyn_module_name mapping."""
        self.alias_to_dyn = alias_map or {}

    def _resolve_tool_fn(self, tool_id: str) -> Optional[Callable[..., Any]]:
        fn = self.registered_tools.get(tool_id)
        if fn is not None:
            return fn

        # Fallback: resolve from "<alias>.<fn_name>"
        try:
            alias, name = tool_id.split(".", 1)
        except ValueError:
            return None

        try:
            # ✅ Use alias_to_dyn map to get real module name
            dyn_module_name = self.alias_to_dyn.get(alias)
            if not dyn_module_name:
                self.log.log(f"[supervisor] No mapping for alias '{alias}'", level="ERROR")
                return None

            # Import the actual dyn module
            import importlib
            try:
                mod = importlib.import_module(dyn_module_name)
            except ImportError:
                self.log.log(f"[supervisor] Could not import module '{dyn_module_name}'", level="ERROR")
                return None

            owner = getattr(mod, "tools", mod)
            fn = getattr(owner, name, None)
            if callable(fn):
                self.registered_tools[tool_id] = fn
                return fn
            else:
                self.log.log(f"[supervisor] Function '{name}' not found or not callable in '{dyn_module_name}'", level="ERROR")
        except Exception as e:
            self.log.log(f"[supervisor] Exception resolving tool '{tool_id}': {e}", level="ERROR")
            return None

        return None

    @staticmethod
    def _decode_params(params: dict) -> dict:
        """Recursively decode base64-encoded bytes values."""
        if not isinstance(params, dict):
            return params

        result = {}
        for key, value in params.items():
            if isinstance(value, dict):
                # Check for special bytes marker
                if value.get("__type__") == "bytes" and "__data__" in value:
                    try:
                        result[key] = base64.b64decode(value["__data__"])
                    except Exception:
                        result[key] = value  # Fall back to original on decode error
                else:
                    result[key] = PrivilegedSupervisor._decode_params(value)
            elif isinstance(value, list):
                result[key] = [
                    PrivilegedSupervisor._decode_params(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

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

            # Enforce UID (uid=1001 is executor)
            if uid != 1001:
                conn.sendall(b'{"ok": false, "error": "Wrong UID"}')
                return

            # Read complete request (loop until EOF from client's shutdown)
            chunks = []
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)

            data = b"".join(chunks)
            if not data:
                conn.sendall(b'{"ok": false, "error": "Empty request"}')
                return

            request = json.loads(data.decode("utf-8"))
            result = self.execute_privileged_operation(request)

            # Send complete response
            response = json.dumps(result, ensure_ascii=False).encode("utf-8")
            conn.sendall(response)

        except json.JSONDecodeError as e:
            try:
                conn.sendall(json.dumps({"ok": False, "error": f"Invalid JSON: {e}"}).encode("utf-8"))
            except Exception:
                pass
        except Exception as e:
            try:
                conn.sendall(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    async def execute_privileged_operation(self, request: dict) -> dict:
        """Execute tool in supervisor context with full privileges."""
        tool_id = request.get("tool_id")
        params = request.get("params") or {}
        reason = request.get("reason") or ""

        self.log.log(
            f"[supervisor] Received request: tool_id={tool_id}, "
            f"params_keys={list(params.keys()) if isinstance(params, dict) else 'not-dict'}, "
            f"reason={reason[:50] if reason else 'none'}",
            level="INFO"
        )

        if not tool_id:
            self.log.log("[supervisor] ERROR: Missing tool_id in request", level="ERROR")
            return {"ok": False, "error": "Missing tool_id"}

        fn = self._resolve_tool_fn(tool_id)
        if fn is None:
            self.log.log(
                f"[supervisor] ERROR: Could not resolve callable for {tool_id}. "
                f"Alias map has: {list(self.alias_to_dyn.keys())}",
                level="ERROR"
            )
            return {"ok": False, "error": f"Could not resolve tool callable for {tool_id}"}

        self.log.log(f"[supervisor] Resolved {tool_id} to {fn}", level="INFO")

        # Decode any base64-encoded bytes in params
        decoded_params = self._decode_params(params)

        try:
            self.log.log(f"[supervisor] Executing {tool_id}...", level="INFO")

            # Direct await - we're already in an async context!
            out = await agent_io_tools.tool_call(
                fn=fn,
                params=decoded_params,
                call_reason=reason,
                tool_id=tool_id,
            )

            self.log.log(
                f"[supervisor] Tool {tool_id} completed successfully, "
                f"result type={type(out).__name__}",
                level="INFO"
            )
            return {"ok": True, "result": out}
        except Exception as e:
            self.log.log(f"[supervisor] Tool {tool_id} failed: {e}", level="ERROR")
            import traceback
            self.log.log(f"[supervisor] Traceback:\n{traceback.format_exc()}", level="ERROR")
            return {"ok": False, "error": str(e)}

    async def handle_stream(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Async version for asyncio.start_unix_server."""
        try:
            sock = writer.get_extra_info("socket")
            if sock is None:
                self.log.log("[supervisor] ERROR: No underlying socket", level="ERROR")
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

            self.log.log(f"[supervisor] Connection from PID={pid}, UID={uid}, GID={gid}", level="INFO")

            # Enforce UID (executor runs as 1001)
            if uid != 1001:
                self.log.log(f"[supervisor] Rejected connection from UID {uid} (expected 1001)", level="WARNING")
                writer.write(b'{"ok": false, "error": "Wrong UID"}')
                await writer.drain()
                return

            # Read complete request until EOF
            chunks = []
            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)

            data = b"".join(chunks)
            if not data:
                self.log.log("[supervisor] ERROR: Empty request received", level="ERROR")
                writer.write(b'{"ok": false, "error": "Empty request"}')
                await writer.drain()
                return

            self.log.log(f"[supervisor] Received {len(data)} bytes of request data", level="INFO")

            request = json.loads(data.decode("utf-8"))

            # ✅ Await the async method
            result = await self.execute_privileged_operation(request)

            # Send complete response
            response = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.log.log(f"[supervisor] Sending {len(response)} bytes response", level="INFO")
            writer.write(response)
            await writer.drain()

        except json.JSONDecodeError as e:
            self.log.log(f"[supervisor] JSON decode error: {e}", level="ERROR")
            try:
                writer.write(json.dumps({"ok": False, "error": f"Invalid JSON: {e}"}).encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        except Exception as e:
            self.log.log(f"[supervisor] Unexpected error: {e}", level="ERROR")
            import traceback
            self.log.log(f"[supervisor] Traceback:\n{traceback.format_exc()}", level="ERROR")
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