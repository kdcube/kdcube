"""Small client for observing and driving a real KDCube chat turn over SSE."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence

import httpx
import yaml


DEFAULT_WORKDIR_ROOT = Path.home() / ".kdcube" / "kdcube-runtime"
DEFAULT_EXEC_BUILD_COMMAND = (
    "docker build -t py-code-exec:latest "
    "-f app/ai-app/deployment/docker/all_in_one_kdcube/Dockerfile_Exec app/ai-app"
)


class DemoError(RuntimeError):
    """An actionable demonstration failure."""


@dataclass(frozen=True)
class AgentTarget:
    adapter: str
    bundle_id: str
    agent_id: str
    needs_exec_image: bool
    description: str
    required_operations: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeDescriptor:
    workdir: Path
    tenant: str
    project: str
    base_url: str
    exec_image: str | None
    bundle_ids: frozenset[str]
    bundle_configs: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class LaneEvent:
    route: str
    payload: Mapping[str, Any]
    received_at: str

    @property
    def type(self) -> str:
        return str(self.payload.get("type") or self.route or "")

    @property
    def turn_id(self) -> str:
        conversation = self.payload.get("conversation")
        if isinstance(conversation, Mapping):
            return str(conversation.get("turn_id") or "")
        return ""

    @property
    def conversation_id(self) -> str:
        conversation = self.payload.get("conversation")
        if isinstance(conversation, Mapping):
            return str(conversation.get("conversation_id") or "")
        return ""


@dataclass
class TurnEvidence:
    turn_id: str
    conversation_id: str
    events: list[LaneEvent] = field(default_factory=list)

    @property
    def event_types(self) -> list[str]:
        return [event.type for event in self.events]

    @property
    def answer_text(self) -> str:
        chunks: list[tuple[int, str]] = []
        for event in self.events:
            if event.type != "chat.delta":
                continue
            delta = event.payload.get("delta")
            if not isinstance(delta, Mapping) or str(delta.get("marker") or "answer") != "answer":
                continue
            text = str(delta.get("text") or "")
            if text:
                chunks.append((int(delta.get("index") or 0), text))
        return "".join(text for _index, text in sorted(chunks, key=lambda item: item[0]))

    @property
    def has_accounting(self) -> bool:
        return any(event.type == "accounting.usage" for event in self.events)

    @property
    def has_web_activity(self) -> bool:
        needles = ("web_search", "web.search", "web_fetch", "web.fetch", "websearch", "webfetch")
        for event in self.events:
            if event.type in {"chat.start", "chat.delta", "chat.complete", "chat.error"}:
                continue
            event_data = event.payload.get("event")
            event_data = event_data if isinstance(event_data, Mapping) else {}
            detail = event_data.get("data")
            detail = detail if isinstance(detail, Mapping) else {}
            payload_data = event.payload.get("data")
            payload_data = payload_data if isinstance(payload_data, Mapping) else {}
            fields = (
                event.type,
                event.route,
                str(event_data.get("agent") or ""),
                str(event_data.get("step") or ""),
                str(event_data.get("title") or ""),
                str(detail.get("tool") or ""),
                str(payload_data.get("tool") or ""),
                # Native ReAct tool events name the tool here only.
                str(payload_data.get("tool_id") or ""),
            )
            haystack = " ".join(fields).lower()
            if any(needle in haystack for needle in needles):
                return True
        return False

    @property
    def hosted_file_names(self) -> set[str]:
        names: set[str] = set()
        for event in self.events:
            if event.type != "chat.files":
                continue
            _collect_file_names(event.payload.get("data"), names)
        return names

    @property
    def error_message(self) -> str:
        for event in reversed(self.events):
            if event.type != "chat.error":
                continue
            data = event.payload.get("data")
            if isinstance(data, Mapping):
                return str(data.get("error") or data.get("message") or "chat.error")
            return "chat.error"
        return ""

    def require_baseline(self) -> None:
        missing: list[str] = []
        if not self.answer_text.strip():
            missing.append("answer deltas")
        if not self.has_accounting:
            missing.append("accounting.usage")
        if self.error_message:
            raise DemoError(f"turn {self.turn_id} failed: {self.error_message}")
        if missing:
            raise DemoError(
                f"turn {self.turn_id} completed without required evidence: {', '.join(missing)}. "
                "Inspect the saved events.jsonl and processor logs."
            )


def _collect_file_names(value: Any, out: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"filename", "name", "path", "physical_path", "logical_path"}:
                text = str(child or "").strip()
                if text:
                    out.add(Path(text).name)
            _collect_file_names(child, out)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _collect_file_names(child, out)


def discover_workdir(explicit: str | Path | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not (path / "config" / "assembly.yaml").is_file():
            raise DemoError(f"Not a KDCube runtime workdir: {path}")
        return path
    candidates = sorted(
        path for path in DEFAULT_WORKDIR_ROOT.glob("*")
        if (path / "config" / "assembly.yaml").is_file()
    )
    if len(candidates) == 1:
        return candidates[0].resolve()
    if not candidates:
        raise DemoError("No local KDCube runtime found. Pass --workdir explicitly.")
    rendered = "\n  ".join(str(path) for path in candidates)
    raise DemoError(f"Several runtimes exist. Pass --workdir explicitly:\n  {rendered}")


def load_runtime_descriptor(workdir: str | Path, *, base_url: str | None = None) -> RuntimeDescriptor:
    root = Path(workdir).expanduser().resolve()
    assembly_path = root / "config" / "assembly.yaml"
    bundles_path = root / "config" / "bundles.yaml"
    try:
        assembly = yaml.safe_load(assembly_path.read_text(encoding="utf-8")) or {}
        bundles = yaml.safe_load(bundles_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise DemoError(f"Runtime descriptor is missing: {exc.filename}") from exc
    except yaml.YAMLError as exc:
        raise DemoError(f"Runtime descriptor is not valid YAML: {exc}") from exc

    context = assembly.get("context") if isinstance(assembly.get("context"), Mapping) else {}
    ports = assembly.get("ports") if isinstance(assembly.get("ports"), Mapping) else {}
    tenant = str(context.get("tenant") or "").strip()
    project = str(context.get("project") or "").strip()
    ingress_port = str(ports.get("ingress") or "").strip()
    if not tenant or not project:
        raise DemoError(f"{assembly_path} must define context.tenant and context.project")
    if not base_url and not ingress_port:
        raise DemoError(f"{assembly_path} must define ports.ingress, or pass --base-url")

    exec_cfg: Any = assembly
    for key in ("platform", "services", "proc", "exec"):
        exec_cfg = exec_cfg.get(key) if isinstance(exec_cfg, Mapping) else None
    exec_image = str((exec_cfg or {}).get("py_code_exec_image") or "").strip() or None

    bundle_root = bundles.get("bundles") if isinstance(bundles.get("bundles"), Mapping) else {}
    items = bundle_root.get("items") if isinstance(bundle_root, Mapping) else []
    bundle_configs: dict[str, Mapping[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        bundle_id = str(item.get("id") or "").strip()
        if not bundle_id:
            continue
        config = item.get("config")
        bundle_configs[bundle_id] = config if isinstance(config, Mapping) else {}
    bundle_ids = frozenset(bundle_configs)
    resolved_url = (base_url or f"http://localhost:{ingress_port}").rstrip("/")
    return RuntimeDescriptor(
        workdir=root,
        tenant=tenant,
        project=project,
        base_url=resolved_url,
        exec_image=exec_image,
        bundle_ids=bundle_ids,
        bundle_configs=bundle_configs,
    )


def _normalize_bearer_token(value: Any) -> str:
    token = str(value or "").strip()
    if token[:7].lower() == "bearer ":
        token = token[7:].strip()
    if not token:
        raise DemoError("The bearer token is empty.")
    return token


def load_bearer_token(token_file: str | Path | None) -> str:
    if token_file:
        path = Path(token_file).expanduser()
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise DemoError(f"Token file must not be group/world accessible. Run: chmod 600 {path}")
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            for key in ("access_token", "accessToken", "token", "bearer_token"):
                value = str(data.get(key) or "").strip() if isinstance(data, Mapping) else ""
                if value:
                    return _normalize_bearer_token(value)
        if raw:
            return _normalize_bearer_token(raw)
        raise DemoError(f"Token file is empty: {token_file}")

    import getpass

    value = getpass.getpass("KDCube bearer token (input is hidden): ")
    try:
        return _normalize_bearer_token(value)
    except DemoError as exc:
        raise DemoError(
            "A signed-in bearer token is required. Use --token-file or enter one at the prompt."
        ) from exc


def require_bundle(runtime: RuntimeDescriptor, target: AgentTarget) -> None:
    if target.bundle_id not in runtime.bundle_ids:
        raise DemoError(
            f"Bundle {target.bundle_id!r} is not staged in {runtime.workdir / 'config/bundles.yaml'}. "
            f"Follow {target.adapter}'s README setup step, then run `kdcube bundle status --live`."
        )
    config = runtime.bundle_configs.get(target.bundle_id) or {}
    surfaces = config.get("surfaces") if isinstance(config, Mapping) else {}
    surfaces = surfaces if isinstance(surfaces, Mapping) else {}
    provider = surfaces.get("as_provider")
    provider = provider if isinstance(provider, Mapping) else {}
    provider_bundle = provider.get("bundle")
    provider_bundle = provider_bundle if isinstance(provider_bundle, Mapping) else {}
    if provider_bundle.get("default_chat") is not True:
        raise DemoError(
            f"Bundle {target.bundle_id!r} must declare surfaces.as_provider.bundle.default_chat: true."
        )

    consumer = surfaces.get("as_consumer")
    consumer = consumer if isinstance(consumer, Mapping) else {}
    agents = consumer.get("agents")
    agents = agents if isinstance(agents, Mapping) else {}
    agent = agents.get(target.agent_id)
    if not isinstance(agent, Mapping):
        raise DemoError(
            f"Bundle {target.bundle_id!r} does not declare target agent {target.agent_id!r} "
            "under surfaces.as_consumer.agents."
        )

    declared_operations: set[str] = set()
    tools = agent.get("tools")
    for tool in tools if isinstance(tools, Sequence) and not isinstance(tools, str) else ():
        if not isinstance(tool, Mapping):
            continue
        allowed = tool.get("allowed")
        if isinstance(allowed, str):
            declared_operations.update(part.strip() for part in allowed.split(",") if part.strip())
        elif isinstance(allowed, Sequence):
            declared_operations.update(str(part).strip() for part in allowed if str(part).strip())

    agent_runtime = config.get("agent") if isinstance(config, Mapping) else {}
    agent_runtime = agent_runtime if isinstance(agent_runtime, Mapping) else {}
    allowed_tools = agent_runtime.get("allowed_tools")
    if isinstance(allowed_tools, str):
        declared_operations.update(part.strip() for part in allowed_tools.split(",") if part.strip())
    elif isinstance(allowed_tools, Sequence):
        declared_operations.update(str(part).strip() for part in allowed_tools if str(part).strip())

    missing = sorted(set(target.required_operations) - declared_operations)
    if missing:
        raise DemoError(
            f"Bundle {target.bundle_id!r} agent {target.agent_id!r} is missing required operations: "
            f"{', '.join(missing)}. Merge the adapter's config/bundles.patch.yaml into the active descriptor."
        )


def require_exec_image(runtime: RuntimeDescriptor, *, repo_root: Path) -> None:
    image = runtime.exec_image
    if not image:
        raise DemoError(
            "The runtime has no platform.services.proc.exec.py_code_exec_image. "
            "Add it to assembly.yaml before running a generated-code demonstration."
        )
    if shutil.which("docker") is None:
        raise DemoError("Docker is not installed or not on PATH; the isolated Python runtime cannot be checked.")
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise DemoError(
            f"Execution image {image!r} is unavailable. From {repo_root}, run:\n  {DEFAULT_EXEC_BUILD_COMMAND}"
        )


async def iter_sse_frames(response: httpx.Response) -> AsyncIterator[tuple[str, Mapping[str, Any]]]:
    event_name = "message"
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise DemoError(f"Malformed SSE JSON for event {event_name!r}: {exc}") from exc
                if isinstance(payload, Mapping):
                    yield event_name, payload
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)


class RuntimeChatClient:
    def __init__(
        self,
        runtime: RuntimeDescriptor,
        *,
        bearer_token: str,
        evidence_path: Path,
        raw_events: bool = False,
        connect_timeout: float = 15.0,
        accounting_grace_seconds: float = 10.0,
    ) -> None:
        self.runtime = runtime
        self.bearer_token = bearer_token
        self.evidence_path = evidence_path
        self.raw_events = raw_events
        self.connect_timeout = connect_timeout
        self.accounting_grace_seconds = accounting_grace_seconds
        self.stream_id = f"harness-demo-{uuid.uuid4().hex}"
        self._queue: asyncio.Queue[LaneEvent | BaseException] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._stream_failure: BaseException | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RuntimeChatClient":
        timeout = httpx.Timeout(connect=self.connect_timeout, read=None, write=30.0, pool=30.0)
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        self._stream_task = asyncio.create_task(self._read_stream())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.connect_timeout)
        except TimeoutError as exc:
            await self.aclose()
            raise DemoError(f"Timed out opening {self.runtime.base_url}/sse/stream") from exc
        if self._stream_failure is not None:
            failure = self._stream_failure
            await self.aclose()
            raise DemoError(f"Could not open the SSE stream: {failure}") from failure
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stream_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _read_stream(self) -> None:
        assert self._client is not None
        params = {
            "stream_id": self.stream_id,
            "bearer_token": self.bearer_token,
            "tenant": self.runtime.tenant,
            "project": self.runtime.project,
        }
        headers = {"Authorization": f"Bearer {self.bearer_token}", "Accept": "text/event-stream"}
        try:
            async with self._client.stream(
                "GET", f"{self.runtime.base_url}/sse/stream", params=params, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise DemoError(f"SSE stream failed ({response.status_code}): {body[:500]}")
                async for route, payload in iter_sse_frames(response):
                    event = LaneEvent(
                        route=route,
                        payload=payload,
                        received_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self._write_event(event)
                    if route == "ready":
                        self._ready.set()
                    else:
                        self._print_event(event)
                        await self._queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._stream_failure = exc
            self._ready.set()
            await self._queue.put(exc)

    def _write_json(self, row: Mapping[str, Any]) -> None:
        with self.evidence_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True, default=str) + "\n")

    def _write_event(self, event: LaneEvent) -> None:
        self._write_json(
            {
                "kind": "sse",
                "route": event.route,
                "received_at": event.received_at,
                "payload": event.payload,
            }
        )

    def _print_event(self, event: LaneEvent) -> None:
        if self.raw_events:
            print(json.dumps({"route": event.route, "payload": event.payload}, ensure_ascii=False))
            return
        event_data = event.payload.get("event")
        event_data = event_data if isinstance(event_data, Mapping) else {}
        status = str(event_data.get("status") or "")
        step = str(event_data.get("step") or "")
        title = str(event_data.get("title") or "")
        suffix = " | ".join(value for value in (status, step, title) if value)
        if event.type == "chat.delta":
            delta = event.payload.get("delta")
            text = str(delta.get("text") or "") if isinstance(delta, Mapping) else ""
            if text:
                print(text, end="", flush=True)
            return
        if event.type == "accounting.usage":
            data = event.payload.get("data")
            data = data if isinstance(data, Mapping) else {}
            cost = data.get("cost_usd") or data.get("total_cost_usd") or data.get("usd")
            suffix = f"{suffix} | cost_usd={cost}" if cost is not None else suffix
        print(f"\n[{event.route}] {event.type}" + (f" | {suffix}" if suffix else ""))

    async def submit_turn(
        self,
        *,
        target: AgentTarget,
        text: str,
        conversation_id: str | None,
        timeout_seconds: float,
    ) -> TurnEvidence:
        assert self._client is not None
        event_id = f"evt_{uuid.uuid4().hex}"
        event_submission: dict[str, Any] = {
            "external_events": [
                {
                    "event_id": event_id,
                    "type": "event.user.prompt",
                    "event_source_id": "event.user.prompt",
                    "agent_id": target.agent_id,
                    "reactive": True,
                    "payload": {"mime": "text/plain", "event": {"text": text}},
                }
            ],
            "chat_history": [],
            "tenant": self.runtime.tenant,
            "project": self.runtime.project,
            "bundle_id": target.bundle_id,
            "target": {"agent_id": target.agent_id, "agent": target.agent_id},
        }
        if conversation_id:
            event_submission["conversation_id"] = conversation_id
        response = await self._client.post(
            f"{self.runtime.base_url}/sse/chat",
            params={"stream_id": self.stream_id},
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            },
            json=event_submission,
        )
        if response.status_code >= 400:
            raise DemoError(f"Chat submit failed ({response.status_code}): {response.text[:1000]}")
        ack = response.json()
        turn_id = str(ack.get("turn_id") or ack.get("active_turn_id") or "").strip()
        resolved_conversation = str(ack.get("conversation_id") or conversation_id or "").strip()
        if not turn_id or not resolved_conversation:
            raise DemoError(f"Chat acknowledgement omitted turn/conversation identity: {ack}")
        self._write_json({"kind": "ack", "payload": ack})
        print(f"\n\n[submitted] conversation={resolved_conversation} turn={turn_id}")
        return await self._collect_turn(
            turn_id=turn_id,
            conversation_id=resolved_conversation,
            timeout_seconds=timeout_seconds,
        )

    async def _collect_turn(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        timeout_seconds: float,
    ) -> TurnEvidence:
        evidence = TurnEvidence(turn_id=turn_id, conversation_id=conversation_id)
        deadline = time.monotonic() + timeout_seconds
        completed = False
        while not completed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DemoError(
                    f"Timed out after {timeout_seconds:.0f}s waiting for turn {turn_id}. "
                    f"Evidence: {self.evidence_path}"
                )
            item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            if isinstance(item, BaseException):
                raise DemoError(f"SSE stream ended while waiting for {turn_id}: {item}") from item
            if item.turn_id != turn_id:
                continue
            evidence.events.append(item)
            completed = item.type in {"chat.complete", "chat.error"}
        if item.type == "chat.complete" and not evidence.has_accounting:
            await self._collect_late_accounting(evidence, turn_id=turn_id)
        return evidence

    async def _collect_late_accounting(self, evidence: TurnEvidence, *, turn_id: str) -> None:
        # A run-to-completion lane emits chat.complete from its own loop, and the
        # economics door emits accounting.usage only after it settles the turn.
        deadline = time.monotonic() + self.accounting_grace_seconds
        while not evidence.has_accounting:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            if isinstance(item, BaseException):
                return
            if item.turn_id == turn_id:
                evidence.events.append(item)


def default_research_prompt() -> str:
    return (
        "Research the current stable Python release using web search and primary python.org sources. "
        "Report the version, release date, and three release highlights with source links. "
        "Do not create files yet."
    )


def default_artifact_prompt() -> str:
    return (
        "Use the research from the previous turn. Create exactly two polished files named "
        "agent-harness-research.pdf and agent-harness-research.xlsx. The PDF must summarize the "
        "release with clickable source URLs. The workbook must have a Summary sheet and a Sources "
        "sheet. Publish both files to this conversation and briefly describe them."
    )


def validate_demonstration(first: TurnEvidence, second: TurnEvidence) -> None:
    first.require_baseline()
    second.require_baseline()
    if first.conversation_id != second.conversation_id:
        raise DemoError(
            "The second turn did not continue the first conversation: "
            f"{first.conversation_id!r} != {second.conversation_id!r}"
        )
    if not first.has_web_activity:
        raise DemoError(
            "The research turn completed without a web-search/fetch activity event. "
            "A prose claim is not evidence that the governed web tool ran."
        )
    names = {name.lower() for name in second.hosted_file_names}
    required = {"agent-harness-research.pdf", "agent-harness-research.xlsx"}
    missing = sorted(required - names)
    if missing:
        raise DemoError(
            "The artifact turn did not publish the required chat.files entries: "
            + ", ".join(missing)
        )
