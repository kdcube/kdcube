"""
LSP JSON-RPC client for communicating with language servers via stdio.
Supports Pyright (Python) and tsserver (TypeScript).
"""

import json
import logging
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("coding-core-mcp")


class LSPClient:
    """JSON-RPC client over stdio for LSP servers."""

    def __init__(self, command: str, args: list[str], cwd: str):
        self._command = command
        self._args = args
        self._cwd = cwd
        self._process = None
        self._request_id = 0
        self._responses = {}
        self._lock = threading.Lock()
        self._reader_thread = None
        self._initialized = False

    def start(self, timeout: float = 60.0):
        """Start the language server process and initialize the LSP session."""
        log.info("[LSP] Starting %s %s in %s", self._command, self._args, self._cwd)

        self._process = subprocess.Popen(
            [self._command] + self._args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
        )

        # Start reader thread for responses
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

        # Initialize LSP session
        root_uri = Path(self._cwd).as_uri()
        init_result = self.request("initialize", {
            "processId": None,
            "rootUri": root_uri,
            "rootPath": self._cwd,
            "capabilities": {
                "textDocument": {
                    "references": {"dynamicRegistration": False},
                    "definition": {"dynamicRegistration": False},
                    "typeHierarchy": {},
                    "callHierarchy": {},
                },
                "workspace": {
                    "symbol": {"dynamicRegistration": False},
                },
            },
        }, timeout=timeout)

        self.notify("initialized", {})
        self._initialized = True
        log.info("[LSP] Server initialized: %s", self._command)
        return init_result

    def shutdown(self):
        """Gracefully shut down the LSP server."""
        if self._process and self._process.poll() is None:
            try:
                self.request("shutdown", None, timeout=5.0)
                self.notify("exit", None)
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5.0)
            except Exception:
                self._process.kill()
        self._initialized = False
        log.info("[LSP] Server shut down")

    def request(self, method: str, params, timeout: float = 30.0):
        """Send a JSON-RPC request and wait for the response."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        self._send(message)

        # Wait for response
        deadline = time.time() + timeout
        while time.time() < deadline:
            if req_id in self._responses:
                resp = self._responses.pop(req_id)
                if "error" in resp:
                    raise LSPError(resp["error"].get("message", "Unknown LSP error"),
                                   resp["error"].get("code"))
                return resp.get("result")
            time.sleep(0.05)

        raise TimeoutError(f"LSP request '{method}' timed out after {timeout}s")

    def notify(self, method: str, params):
        """Send a JSON-RPC notification (no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self._send(message)

    def _send(self, message: dict):
        """Send a JSON-RPC message with Content-Length header."""
        body = json.dumps(message)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        raw = (header + body).encode("utf-8")
        self._process.stdin.write(raw)
        self._process.stdin.flush()

    def _read_loop(self):
        """Background thread reading JSON-RPC responses from stdout."""
        while self._process and self._process.poll() is None:
            try:
                # Read headers
                headers = {}
                while True:
                    line = self._process.stdout.readline().decode("utf-8").strip()
                    if not line:
                        break
                    if ":" in line:
                        key, val = line.split(":", 1)
                        headers[key.strip()] = val.strip()

                content_length = int(headers.get("Content-Length", 0))
                if content_length == 0:
                    continue

                # Read body
                body = self._process.stdout.read(content_length).decode("utf-8")
                msg = json.loads(body)

                # Route: response (has "id") vs notification (no "id")
                if "id" in msg:
                    self._responses[msg["id"]] = msg
                # Notifications (diagnostics, progress) are ignored
            except Exception:
                if self._process and self._process.poll() is not None:
                    break

    # ----- LSP convenience methods -----

    def workspace_symbols(self, query: str = "") -> list[dict]:
        """Discover all symbols in the workspace."""
        return self.request("workspace/symbol", {"query": query}, timeout=60.0) or []

    def text_document_definition(self, file_uri: str, line: int, char: int):
        """Go to definition."""
        return self.request("textDocument/definition", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": char},
        })

    def text_document_references(self, file_uri: str, line: int, char: int):
        """Find all references to the symbol at position."""
        return self.request("textDocument/references", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": char},
            "context": {"includeDeclaration": False},
        })

    def type_hierarchy_supertypes(self, item: dict):
        """Get supertypes (parent classes) of a type hierarchy item."""
        return self.request("typeHierarchy/supertypes", {"item": item})

    def type_hierarchy_subtypes(self, item: dict):
        """Get subtypes (child classes) of a type hierarchy item."""
        return self.request("typeHierarchy/subtypes", {"item": item})

    def prepare_type_hierarchy(self, file_uri: str, line: int, char: int):
        """Prepare type hierarchy at a position (needed before super/subtypes)."""
        return self.request("textDocument/prepareTypeHierarchy", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": char},
        })

    def prepare_call_hierarchy(self, file_uri: str, line: int, char: int):
        """Prepare call hierarchy at a position."""
        return self.request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": char},
        })

    def call_hierarchy_incoming(self, item: dict):
        """Who calls this method/function?"""
        return self.request("callHierarchy/incomingCalls", {"item": item})

    def call_hierarchy_outgoing(self, item: dict):
        """What does this method/function call?"""
        return self.request("callHierarchy/outgoingCalls", {"item": item})

    def did_open(self, file_uri: str, language_id: str, text: str):
        """Notify server that a document was opened (required before some requests)."""
        self.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            },
        })


class LSPError(Exception):
    def __init__(self, message: str, code: int = None):
        super().__init__(message)
        self.code = code
