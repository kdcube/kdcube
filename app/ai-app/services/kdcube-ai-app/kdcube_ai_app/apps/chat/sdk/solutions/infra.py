# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/infra.py

from typing import Callable, Awaitable,Any, Dict, List
import pathlib, json, os

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator


def mk_thinking_streamer(author: str,
                        comm: ChatCommunicator) -> Callable[[str], Awaitable[None]]:
    counter = {"n": 0}
    async def emit_thinking_delta(text: str, completed: bool = False):
        if not text:
            return
        i = counter["n"]; counter["n"] += 1
        # author = f"{self.AGENT_NAME}.{phase}"
        await comm.delta(text=text, index=i, marker="thinking", agent=author, completed=completed)
    return emit_thinking_delta

async def emit_event(comm: ChatCommunicator,
                     etype: str, title: str, step: str,
                     data: Dict[str, Any],
                     agent: str|None = None,
                     status: str = "completed"):

    data = (data or {}).copy()
    await comm.event(
        agent=agent,
        type=etype or "chat.step",
        title=title,
        step=step or "event",
        data=data,
        markdown=None,
        status=status or "update",
    )

def collect_outputs(*, output_dir: pathlib.Path, outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"items": []}
    for spec in outputs or []:
        fn = spec.get("filename") or ""
        kind = (spec.get("kind") or "json").lower()
        key = spec.get("key")
        p = (output_dir / fn)
        item = {"filename": fn, "present": p.exists()}
        if p.exists():
            try:
                if kind == "json":
                    item["data"] = json.loads(p.read_text(encoding="utf-8"))
                elif kind == "text":
                    item["data"] = p.read_text(encoding="utf-8")
                else:
                    item["size"] = p.stat().st_size
                    item["data"] = None
            except Exception as e:
                item["error"] = f"{type(e).__name__}: {e}"
        if key:
            item["key"] = key
        out["items"].append(item)
    return out

def get_exec_workspace_root() -> pathlib.Path:
    """
    Return the appropriate root directory for execution workspaces.

    Returns:
        - Docker-in-Docker: /exec-workspace (shared with host via volume mount)
        - Bare metal: /tmp (local filesystem only)

    This ensures temporary execution directories are created in a location
    that's accessible to sibling containers when running Docker-in-Docker.
    """
    if _is_running_in_docker():
        return pathlib.Path("/exec-workspace")
    else:
        return pathlib.Path("/tmp")


def _is_running_in_docker() -> bool:
    """
    Detect if we're running inside a Docker container.

    Uses multiple detection methods for reliability:
    1. Presence of /.dockerenv file
    2. Docker in /proc/1/cgroup
    3. DOCKER_CONTAINER environment variable
    """
    # Method 1: Check for .dockerenv file
    if os.path.exists("/.dockerenv"):
        return True

    # Method 2: Check cgroup for docker
    try:
        with open("/proc/1/cgroup", "r") as f:
            return "docker" in f.read()
    except Exception:
        pass

    # Method 3: Check environment variable
    if os.environ.get("DOCKER_CONTAINER") == "true":
        return True

    return False