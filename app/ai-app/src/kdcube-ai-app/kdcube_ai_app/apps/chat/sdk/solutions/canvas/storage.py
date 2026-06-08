from __future__ import annotations

import json
import logging
import pathlib
import re
import time
from typing import Any, Dict, Mapping

from kdcube_ai_app.apps.chat.sdk.storage.bundle_artifact_storage import BundleArtifactStorage
from kdcube_ai_app.storage.observed_file_locks import observed_file_lock

from .ids import timestamp_id, timestamp_slug_id
from .storage_utils import safe_storage_segment


CANVAS_SCHEMA = "kdcube.canvas.v1"
CANVAS_PATCH_SCHEMA = "kdcube.canvas.patch.v1"
CANVAS_MIME = "application/vnd.kdcube.canvas+json;version=1"
CANVAS_PATCH_MIME = "application/vnd.kdcube.canvas.patch+json;version=1"
DEFAULT_CANVAS_NAME = "main"
LOGGER = logging.getLogger("kdcube.sdk.solutions.canvas.storage")
_CANVAS_DURABLE_FI_RE = re.compile(r"^fi:conv_[^.]+\.turn_[^.]+\.")


def _decode_json(raw: Any) -> Dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
    except Exception:
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def parse_canvas_uri(uri: Any) -> Dict[str, Any]:
    raw = str(uri or "").strip()
    if not raw:
        return {}
    if raw.startswith("canvas:"):
        body = raw.split(":", 1)[1].strip()
        revision: int | None = None
        if "@" in body:
            name, revision_text = body.rsplit("@", 1)
            revision_text = revision_text.strip().lower()
            if revision_text and revision_text != "latest":
                revision = int(revision_text)
        else:
            name = body
        return {
            "scheme": "canvas",
            "uri": raw,
            "canvas_name": safe_storage_segment(name or DEFAULT_CANVAS_NAME, default=DEFAULT_CANVAS_NAME),
            "revision": revision,
        }
    if raw.startswith("ext:"):
        return {
            "scheme": "ext",
            "uri": raw,
            "key": raw.split(":", 1)[1].strip().lstrip("/"),
        }
    return {"scheme": "", "uri": raw}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _canvas_map(
    *,
    legend: list[Dict[str, Any]],
    bounds: Mapping[str, Any],
    cols: int = 24,
    rows: int = 12,
) -> list[str]:
    bx = _num(bounds.get("x"), 0.0)
    by = _num(bounds.get("y"), 0.0)
    bw = max(1.0, _num(bounds.get("w"), 1600.0))
    bh = max(1.0, _num(bounds.get("h"), 1000.0))
    grid = [[".." for _ in range(cols)] for _ in range(rows)]
    for row in legend:
        if not isinstance(row, Mapping) or str(row.get("placement") or "") != "placed":
            continue
        rect = row.get("rect") if isinstance(row.get("rect"), Mapping) else {}
        if not rect:
            continue
        token = str(row.get("map_label") or row.get("label") or row.get("id") or "??").strip() or "??"
        x = _num(rect.get("x"), bx)
        y = _num(rect.get("y"), by)
        w = max(1.0, _num(rect.get("w"), 1.0))
        h = max(1.0, _num(rect.get("h"), 1.0))
        c0 = max(0, min(cols - 1, int(((x - bx) / bw) * cols)))
        r0 = max(0, min(rows - 1, int(((y - by) / bh) * rows)))
        c1 = max(c0 + 1, min(cols, int(((x + w - bx) / bw) * cols) + 1))
        r1 = max(r0 + 1, min(rows, int(((y + h - by) / bh) * rows) + 1))
        for rr in range(r0, r1):
            for cc in range(c0, c1):
                grid[rr][cc] = token
    return [" ".join(row) for row in grid]


def _legend_text_lines(legend: list[Dict[str, Any]], *, limit: int = 80) -> list[str]:
    lines: list[str] = []
    for row in legend[:limit]:
        if not isinstance(row, Mapping):
            continue
        if row.get("trashed"):
            continue
        label = str(row.get("map_label") or row.get("label") or row.get("id") or "?").strip() or "?"
        card_id = str(row.get("id") or "").strip()
        placement = str(row.get("placement") or "placed").strip() or "placed"
        title = str(row.get("title") or "").strip()
        mime = str(row.get("mime") or "").strip()
        ref = str(row.get("logical_path") or "").strip()
        bits = [
            f"- {label}",
            str(row.get("kind") or "note"),
        ]
        if card_id and card_id != label:
            bits.append(f"card_id={card_id}")
        if placement != "placed":
            bits.append(placement)
        if row.get("selected"):
            bits.append("selected")
        if row.get("suggested") or row.get("placement") == "suggested":
            bits.append("pending_suggestion")
        if row.get("locked"):
            bits.append("locked")
        if row.get("agent_avoid"):
            bits.append("avoid")
        if title:
            bits.append(f"title={title}")
        if mime:
            bits.append(f"mime={mime}")
        description = str(row.get("description") or "").strip().replace("\n", " ")
        if description:
            bits.append("has_description")
        try:
            comments_count = int(row.get("comments_count") or 0)
        except Exception:
            comments_count = 0
        if comments_count > 0:
            bits.append(f"comments={comments_count}")
        if ref:
            bits.append(f"ref={ref}")
        lines.append(" ".join(bits))
        preview = str(row.get("content_preview") or "").strip().replace("\n", " ")
        if preview:
            lines.append(f"  visible: {preview[:500]}")
        if description:
            lines.append(f"  description: {description[:500]}")
    if len(legend) > limit:
        lines.append(f"- ... {len(legend) - limit} more cards")
    return lines


def _changed_cards(canvas: Mapping[str, Any], changed: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    ids: set[str] = set()
    for row in changed:
        if not isinstance(row, Mapping):
            continue
        for key in ("card_id", "created_card_id"):
            value = str(row.get(key) or "").strip()
            if value:
                ids.add(value)
    if not ids:
        return []
    cards: list[Dict[str, Any]] = []
    for card in canvas.get("cards") or []:
        if not isinstance(card, Mapping):
            continue
        if str(card.get("id") or "").strip() in ids:
            cards.append(dict(card))
    return cards


def _string_card_value(card: Mapping[str, Any], key: str) -> str:
    value = card.get(key)
    return str(value or "").strip()


def _task_issue_ref(ref: str) -> str:
    normalized = ref.strip()
    if normalized.startswith("task:issues/"):
        issue_id = normalized[len("task:issues/"):].split("?", 1)[0].split("#", 1)[0].strip("/")
        return f"task:issues/{issue_id}" if issue_id else ""
    if normalized.startswith("task:issue:"):
        issue_id = normalized[len("task:issue:"):].split("?", 1)[0].split("#", 1)[0].strip("/")
        return f"task:issues/{issue_id}" if issue_id else ""
    return ""


def _card_identity_ref(card: Mapping[str, Any]) -> str:
    """Stable identity for proxy/ref cards.

    For proxy cards the target object ref is also the durable card id:
    `task:issues/<id>`, `fi:...`, `ext:...`, `mem:...`, `so:...`, etc. Inline
    `content` cards intentionally return empty here because they create new
    canvas-hosted objects on write.
    """

    if "content" in card:
        return ""
    for key in ("logical_path", "storage_ref", "artifact_ref", "ref", "hosted_uri"):
        ref = _string_card_value(card, key)
        if not ref:
            continue
        task_ref = _task_issue_ref(ref)
        return task_ref or ref
    source_refs = card.get("source_refs") if isinstance(card.get("source_refs"), list) else []
    for raw_ref in source_refs:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        task_ref = _task_issue_ref(ref)
        if task_ref:
            return task_ref
    return ""


def _validate_canvas_proxy_ref(ref: str) -> None:
    value = str(ref or "").strip()
    if value.startswith("fi:") and not _CANVAS_DURABLE_FI_RE.match(value):
        raise ValueError("canvas fi refs must include conv_<conversation_id>")


def _card_id_prefix(kind: Any) -> str:
    value = str(kind or "").strip()
    if value == "user.attachment":
        return "A"
    if value == "user.text":
        return "U"
    if value == "agent.text":
        return "R"
    if value == "file":
        return "F"
    if value == "memory":
        return "M"
    if value in {"source", "search.result"}:
        return "S"
    if value in {"issue.ref", "story.ref", "task.ref"}:
        return "T"
    return "O"


def _canvas_owned_card_prefix(kind: Any) -> str:
    value = str(kind or "").strip()
    if value == "user.text":
        return "ut"
    if value == "user.attachment":
        return "ua"
    if value == "agent.text":
        return "at"
    return "obj"


def _is_canvas_owned_kind(kind: Any) -> bool:
    return str(kind or "").strip() in {"user.text", "user.attachment", "agent.text"}


def _new_card_id(kind: Any) -> str:
    return timestamp_slug_id(_canvas_owned_card_prefix(kind))


def _assign_map_labels(legend: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    counters: dict[str, int] = {}
    labelled: list[Dict[str, Any]] = []
    for row in legend:
        if not isinstance(row, Mapping):
            continue
        copy = dict(row)
        existing = str(copy.get("map_label") or copy.get("label") or "").strip()
        if existing:
            copy["map_label"] = existing
            labelled.append(copy)
            continue
        prefix = _card_id_prefix(copy.get("kind"))
        counters[prefix] = counters.get(prefix, 0) + 1
        copy["map_label"] = f"{prefix}{counters[prefix]}"
        labelled.append(copy)
    return labelled


class CanvasStore:
    """Bundle-owned, user-scoped canvas storage.

    Canvas revisions are documents of pins/cards only. Any supplied text or
    object bytes are stored as separate versioned canvas objects and referenced
    from cards through `ext:` logical paths.
    """

    def __init__(
        self,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        user_id: str,
        storage_root: str | pathlib.Path,
        revision_retention: int = 80,
        artifact_prefix: str = "canvas",
        origin_prefix: str = "sdk.canvas",
        state_event_source_id: str = "canvas.state",
        ui_event_type: str = "canvas.patch.applied",
        artifact_resolver_name: str = "sdk.canvas.bundle_artifact_storage",
        handoff_resolver_names: Mapping[str, str] | None = None,
    ) -> None:
        tenant = str(tenant or "").strip()
        project = str(project or "").strip()
        if not tenant or not project:
            raise RuntimeError("tenant/project context is unavailable")
        self.tenant = tenant
        self.project = project
        self.bundle_id = str(bundle_id or "").strip()
        self.user_id = str(user_id or "anonymous").strip() or "anonymous"
        self.storage_root = pathlib.Path(storage_root)
        self.artifacts = BundleArtifactStorage(tenant=tenant, project=project, bundle_id=self.bundle_id)
        self.revision_retention = max(1, int(revision_retention or 80))
        self.artifact_prefix = safe_storage_segment(artifact_prefix or "canvas", default="canvas")
        self.origin_prefix = str(origin_prefix or "sdk.canvas").strip() or "sdk.canvas"
        self.state_event_source_id = str(state_event_source_id or "canvas.state").strip() or "canvas.state"
        self.ui_event_type = str(ui_event_type or "canvas.patch.applied").strip() or "canvas.patch.applied"
        self.artifact_resolver_name = (
            str(artifact_resolver_name or "sdk.canvas.bundle_artifact_storage").strip()
            or "sdk.canvas.bundle_artifact_storage"
        )
        self.handoff_resolver_names = {
            str(namespace or "").strip(): str(resolver or "").strip()
            for namespace, resolver in dict(handoff_resolver_names or {}).items()
            if str(namespace or "").strip() and str(resolver or "").strip()
        }

    @classmethod
    def from_scope(
        cls,
        scope: Any,
        *,
        bundle_id: str,
        user_id: str | None = None,
        artifact_prefix: str = "canvas",
        origin_prefix: str = "sdk.canvas",
        state_event_source_id: str = "canvas.state",
        ui_event_type: str = "canvas.patch.applied",
        artifact_resolver_name: str = "sdk.canvas.bundle_artifact_storage",
        handoff_resolver_names: Mapping[str, str] | None = None,
        revision_retention: int | None = None,
    ) -> "CanvasStore":
        if isinstance(scope, Mapping):
            getter = scope.get
        else:
            getter = lambda key, default=None: getattr(scope, key, default)
        return cls(
            tenant=str(getter("tenant", "") or ""),
            project=str(getter("project", "") or ""),
            bundle_id=bundle_id,
            user_id=str(user_id or getter("user_id", None) or "anonymous"),
            storage_root=str(getter("storage_root", "") or "."),
            revision_retention=int(revision_retention if revision_retention is not None else (getter("revision_retention", 80) or 80)),
            artifact_prefix=artifact_prefix,
            origin_prefix=origin_prefix,
            state_event_source_id=state_event_source_id,
            ui_event_type=ui_event_type,
            artifact_resolver_name=artifact_resolver_name,
            handoff_resolver_names=handoff_resolver_names,
        )

    def canvas_name(self, value: Any = None) -> str:
        return safe_storage_segment(value or DEFAULT_CANVAS_NAME, default=DEFAULT_CANVAS_NAME)

    def canvas_id(self, *, canvas_name: str, canvas_id: Any = None) -> str:
        explicit = str(canvas_id or "").strip()
        if explicit:
            return explicit
        return f"canvas:{self.user_id}:{self.canvas_name(canvas_name)}"

    def storage_id(self, canvas_id: str) -> str:
        return safe_storage_segment(canvas_id, default=timestamp_id("canvas"))

    def canvas_uri(self, *, canvas_name: str, revision: int | None = None) -> str:
        uri = f"canvas:{self.canvas_name(canvas_name)}"
        if revision is not None:
            uri = f"{uri}@{max(0, int(revision or 0))}"
        return uri

    def base_relpath(self, *, canvas_id: str) -> pathlib.PurePosixPath:
        safe_user = safe_storage_segment(self.user_id, default="anonymous")
        safe_canvas = self.storage_id(canvas_id)
        return pathlib.PurePosixPath(self.artifact_prefix) / "users" / safe_user / "canvases" / safe_canvas

    def manifest_relpath(self) -> str:
        safe_user = safe_storage_segment(self.user_id, default="anonymous")
        return (pathlib.PurePosixPath(self.artifact_prefix) / "users" / safe_user / "canvases" / "index.json").as_posix()

    def latest_relpath(self, *, canvas_id: str) -> str:
        return (self.base_relpath(canvas_id=canvas_id) / "latest.json").as_posix()

    def revision_relpath(self, *, canvas_id: str, revision: int) -> str:
        rev = max(0, int(revision or 0))
        return (self.base_relpath(canvas_id=canvas_id) / "revisions" / f"{rev:06d}.json").as_posix()

    def object_relpath(
        self,
        *,
        canvas_id: str,
        card_id: str,
        version: int,
        kind: str,
        extension: str,
    ) -> str:
        safe_card = safe_storage_segment(card_id, default=timestamp_id("card"))
        safe_kind = safe_storage_segment(kind, default="object")
        safe_ext = safe_storage_segment(extension, default="bin").lstrip(".") or "bin"
        return (
            self.base_relpath(canvas_id=canvas_id)
            / "objects"
            / safe_kind
            / safe_card
            / f"v{max(1, int(version or 1)):06d}.{safe_ext}"
        ).as_posix()

    def lock_path(self, *, canvas_id: str) -> pathlib.Path:
        safe_user = safe_storage_segment(self.user_id, default="anonymous")
        safe_canvas = self.storage_id(canvas_id)
        safe_prefix = safe_storage_segment(self.artifact_prefix, default="canvas")
        return self.storage_root / ".locks" / safe_prefix / "canvas" / safe_user / f"{safe_canvas}.lock"

    def _read_json(self, relpath: str) -> tuple[bool, Dict[str, Any]]:
        try:
            raw = self.artifacts.read(relpath)
        except Exception:
            return False, {}
        return True, _decode_json(raw)

    def default_document(self, *, canvas_id: str, canvas_name: str, story_id: str = "") -> Dict[str, Any]:
        now = int(time.time())
        return {
            "schema": CANVAS_SCHEMA,
            "owner_user_id": self.user_id,
            "story_id": str(story_id or ""),
            "canvas_name": self.canvas_name(canvas_name),
            "canvas_id": canvas_id,
            "revision": 0,
            "created_at": now,
            "updated_at": now,
            "bounds": {"x": 0, "y": 0, "w": 1600, "h": 1000},
            "cards": [],
            "history": [],
        }

    def normalize_document(
        self,
        raw: Mapping[str, Any],
        *,
        canvas_id: str,
        canvas_name: str,
        story_id: str = "",
    ) -> Dict[str, Any]:
        doc = dict(raw or {})
        normalized_canvas_name = self.canvas_name(doc.get("canvas_name") or canvas_name)
        doc["schema"] = str(doc.get("schema") or CANVAS_SCHEMA)
        doc["owner_user_id"] = str(doc.get("owner_user_id") or self.user_id)
        doc["story_id"] = str(doc.get("story_id") or story_id or "")
        doc["canvas_name"] = normalized_canvas_name
        doc["canvas_id"] = str(doc.get("canvas_id") or canvas_id or self.canvas_id(canvas_name=normalized_canvas_name))
        try:
            doc["revision"] = max(0, int(doc.get("revision") or 0))
        except Exception:
            doc["revision"] = 0
        bounds = doc.get("bounds") if isinstance(doc.get("bounds"), Mapping) else {}
        doc["bounds"] = {
            "x": float(bounds.get("x") or 0),
            "y": float(bounds.get("y") or 0),
            "w": float(bounds.get("w") or 1600),
            "h": float(bounds.get("h") or 1000),
        }
        cards = doc.get("cards") if isinstance(doc.get("cards"), list) else []
        doc["cards"] = self._dedupe_cards_by_identity(
            [dict(card) for card in cards if isinstance(card, Mapping)],
            canvas_id=str(doc.get("canvas_id") or canvas_id),
            canvas_name=normalized_canvas_name,
            revision=int(doc.get("revision") or 0),
        )
        history = doc.get("history") if isinstance(doc.get("history"), list) else []
        doc["history"] = [dict(item) for item in history if isinstance(item, Mapping)][-100:]
        now = int(time.time())
        doc.setdefault("created_at", now)
        doc.setdefault("updated_at", now)
        return doc

    def _dedupe_cards_by_identity(
        self,
        cards: list[Dict[str, Any]],
        *,
        canvas_id: str,
        canvas_name: str,
        revision: int,
    ) -> list[Dict[str, Any]]:
        seen: dict[str, int] = {}
        out: list[Dict[str, Any]] = []
        dropped: list[Dict[str, Any]] = []
        for card in cards:
            identity_ref = _card_identity_ref(card)
            if not identity_ref:
                out.append(card)
                continue
            existing_index = seen.get(identity_ref)
            if existing_index is None:
                seen[identity_ref] = len(out)
                out.append(card)
                continue

            existing = out[existing_index]
            existing_ts = int(_num(existing.get("updated_at") or existing.get("created_at"), 0))
            incoming_ts = int(_num(card.get("updated_at") or card.get("created_at"), 0))
            existing_trashed = bool(existing.get("trashed")) or str(existing.get("placement") or "") == "trashed"
            incoming_trashed = bool(card.get("trashed")) or str(card.get("placement") or "") == "trashed"
            keep_incoming = (
                False
                if existing_trashed != incoming_trashed and incoming_trashed
                else True
                if existing_trashed != incoming_trashed
                else incoming_ts > existing_ts
            )
            dropped_card = existing if keep_incoming else card
            if keep_incoming:
                out[existing_index] = card
            dropped.append({
                "identity_ref": identity_ref,
                "kept_card_id": out[existing_index].get("id"),
                "dropped_card_id": dropped_card.get("id"),
            })
        if dropped:
            LOGGER.warning(
                "[canvas.cards.dedupe] collapsed duplicate proxy pins user_id=%s canvas_id=%s canvas_name=%s revision=%s dropped=%s",
                self.user_id,
                canvas_id,
                canvas_name,
                revision,
                dropped,
            )
        return out

    def read_document(
        self,
        *,
        canvas_id: str,
        canvas_name: str,
        story_id: str = "",
        revision: int | None = None,
    ) -> tuple[bool, Dict[str, Any]]:
        relpath = (
            self.revision_relpath(canvas_id=canvas_id, revision=revision)
            if revision is not None
            else self.latest_relpath(canvas_id=canvas_id)
        )
        found, data = self._read_json(relpath)
        if not found:
            return False, self.default_document(canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
        return True, self.normalize_document(data, canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)

    def ref_extension(self, mime: str) -> str:
        lowered = str(mime or "").split(";", 1)[0].strip().lower()
        if lowered in {"application/json", CANVAS_MIME.split(";", 1)[0]} or lowered.endswith("+json"):
            return "json"
        if lowered in {"text/markdown", "text/x-markdown"}:
            return "md"
        if lowered.startswith("text/"):
            return "txt"
        if lowered == "image/png":
            return "png"
        if lowered in {"image/jpeg", "image/jpg"}:
            return "jpg"
        if lowered == "application/pdf":
            return "pdf"
        return "bin"

    def _object_kind(self, card: Mapping[str, Any]) -> str:
        kind = str(card.get("kind") or "object")
        if kind == "user.text":
            return "user-text"
        if kind == "agent.text":
            return "agent-text"
        if kind == "user.attachment":
            return "user-attachments"
        return safe_storage_segment(kind.replace(".", "-"), default="objects")

    def host_card_content(
        self,
        *,
        canvas_id: str,
        canvas_name: str,
        story_id: str,
        card: Dict[str, Any],
    ) -> Dict[str, Any]:
        if "content" not in card:
            card.pop("content", None)
            return card
        content = card.get("content")
        mime = str(card.get("mime") or "").strip()
        if isinstance(content, Mapping) and isinstance(content.get("text"), str):
            mime = mime or "text/plain"
            text = str(content.get("text") or "")
            data = text.encode("utf-8")
            preview = text[:240]
        elif isinstance(content, (dict, list)):
            mime = mime or "application/json"
            data = json.dumps(content, indent=2, sort_keys=True).encode("utf-8")
            preview = json.dumps(content, sort_keys=True)[:240]
        elif isinstance(content, str):
            mime = mime or "text/plain"
            data = content.encode("utf-8")
            preview = content[:240]
        else:
            mime = mime or "application/octet-stream"
            data = bytes(content) if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8")
            preview = ""
        card_id = str(card.get("id") or _new_card_id(card.get("kind")))
        try:
            next_version = int(card.get("version") or 0) + 1
        except Exception:
            next_version = 1
        relpath = self.object_relpath(
            canvas_id=canvas_id,
            card_id=card_id,
            version=next_version,
            kind=self._object_kind(card),
            extension=self.ref_extension(mime),
        )
        uri = self.artifacts.write(
            relpath,
            data,
            mime=mime,
            meta={
                "story_id": story_id,
                "owner_user_id": self.user_id,
                "canvas_name": self.canvas_name(canvas_name),
                "canvas_id": canvas_id,
                "card_id": card_id,
                "version": next_version,
                "origin": f"{self.origin_prefix}.object",
            },
        )
        card["id"] = card_id
        card["version"] = next_version
        card["mime"] = mime
        card["logical_path"] = f"ext:{relpath}"
        card["storage_ref"] = f"ext:{relpath}"
        card["storage_uri"] = uri
        card["content_preview"] = preview
        card["content_size"] = len(data)
        card.pop("content", None)
        return card

    def host_attachment_bytes(
        self,
        *,
        canvas_id: str,
        canvas_name: str,
        story_id: str,
        card_id: str,
        filename: str,
        content: bytes,
        mime: str,
        version: int = 1,
    ) -> Dict[str, Any]:
        safe_name = safe_storage_segment(filename, default="attachment.bin")
        ext = safe_name.rsplit(".", 1)[-1] if "." in safe_name else self.ref_extension(mime)
        relpath = self.object_relpath(
            canvas_id=canvas_id,
            card_id=card_id,
            version=version,
            kind="user-attachments",
            extension=ext,
        )
        if not relpath.endswith(f".{ext}"):
            relpath = f"{relpath}.{ext}"
        uri = self.artifacts.write(
            relpath,
            content,
            mime=mime or "application/octet-stream",
            meta={
                "story_id": story_id,
                "owner_user_id": self.user_id,
                "canvas_name": self.canvas_name(canvas_name),
                "canvas_id": canvas_id,
                "card_id": card_id,
                "filename": safe_name,
                "version": version,
                "origin": f"{self.origin_prefix}.attachment",
            },
        )
        return {
            "id": card_id,
            "kind": "user.attachment",
            "title": safe_name,
            "mime": mime or "application/octet-stream",
            "logical_path": f"ext:{relpath}",
            "storage_ref": f"ext:{relpath}",
            "storage_uri": uri,
            "version": version,
            "size": len(content),
        }

    def normalize_card(
        self,
        *,
        canvas_id: str,
        story_id: str,
        canvas_name: str,
        raw: Mapping[str, Any],
    ) -> Dict[str, Any]:
        card = dict(raw or {})
        card.setdefault("kind", "note")
        identity_ref = _card_identity_ref(card)
        _validate_canvas_proxy_ref(identity_ref)
        canvas_owned = _is_canvas_owned_kind(card.get("kind")) or "content" in card
        default_id = _new_card_id(card.get("kind"))
        if identity_ref and not canvas_owned:
            # Proxy pins are identified by the original resolver URI. This keeps
            # ownership with the source subsystem: task:, fi:, mem:, so:, ext:.
            card["id"] = identity_ref
            if not str(card.get("logical_path") or "").strip():
                card["logical_path"] = identity_ref
        else:
            card.setdefault("id", default_id)
            card["id"] = safe_storage_segment(str(card.get("id") or ""), default=default_id)
        card.setdefault("placement", "floating")
        placement = str(card.get("placement") or "floating").strip()
        if placement not in {"floating", "placed", "suggested", "trashed"}:
            placement = "floating"
        card["placement"] = placement
        if isinstance(card.get("rect"), Mapping):
            rect = card["rect"]
            card["rect"] = {
                "x": float(rect.get("x") or 0),
                "y": float(rect.get("y") or 0),
                "w": float(rect.get("w") or 240),
                "h": float(rect.get("h") or 160),
            }
            if placement not in {"floating", "suggested", "trashed"}:
                card["placement"] = "placed"
        if placement == "trashed":
            card["trashed"] = True
        if isinstance(card.get("source_refs"), list):
            card["source_refs"] = [str(item) for item in card["source_refs"] if str(item or "").strip()]
        if isinstance(card.get("source_card_ids"), list):
            card["source_card_ids"] = [str(item) for item in card["source_card_ids"] if str(item or "").strip()]
        card.setdefault("created_at", int(time.time()))
        card["updated_at"] = int(time.time())
        return self.host_card_content(
            canvas_id=canvas_id,
            story_id=story_id,
            canvas_name=canvas_name,
            card=card,
        )

    def card_legend(self, canvas: Mapping[str, Any]) -> list[Dict[str, Any]]:
        rows: list[Dict[str, Any]] = []
        for card in canvas.get("cards") or []:
            if not isinstance(card, Mapping):
                continue
            rect = card.get("rect") if isinstance(card.get("rect"), Mapping) else {}
            try:
                content_size = int(card.get("content_size") or card.get("size") or 0)
            except Exception:
                content_size = 0
            rows.append({
                "id": str(card.get("id") or ""),
                "kind": str(card.get("kind") or "note"),
                "title": str(card.get("title") or card.get("label") or "")[:120],
                "mime": str(card.get("mime") or ""),
                "content_preview": str(card.get("content_preview") or card.get("preview") or "")[:500],
                "description": str(card.get("description") or "")[:500],
                "content_size": content_size,
                "placement": str(card.get("placement") or "floating"),
                "rect": {
                    "x": rect.get("x"),
                    "y": rect.get("y"),
                    "w": rect.get("w"),
                    "h": rect.get("h"),
                } if rect else None,
                "logical_path": str(card.get("logical_path") or card.get("artifact_ref") or ""),
                "source_refs": list(card.get("source_refs") or []) if isinstance(card.get("source_refs"), list) else [],
                "trashed": bool(card.get("trashed")) or str(card.get("placement") or "") == "trashed",
                "trash_state": dict(card.get("trash_state") or {}) if isinstance(card.get("trash_state"), Mapping) else {},
                "selected": bool(card.get("selected")),
                "suggested": bool(card.get("suggested")) or str(card.get("placement") or "") == "suggested",
                "created_by": str(card.get("created_by") or ""),
                "comments_count": len(card.get("comments") or []) if isinstance(card.get("comments"), list) else 0,
                "locked": bool(card.get("locked")),
                "agent_avoid": bool(card.get("agent_avoid")),
            })
        return rows

    def projection(self, canvas: Mapping[str, Any]) -> Dict[str, Any]:
        legend = _assign_map_labels(self.card_legend(canvas))
        active_legend = [row for row in legend if not row.get("trashed")]
        placed = [row for row in active_legend if row.get("placement") == "placed"]
        floating = [row for row in active_legend if row.get("placement") != "placed"]
        suggested = [row for row in legend if row.get("suggested") or row.get("placement") == "suggested"]
        bounds = dict(canvas.get("bounds") or {}) if isinstance(canvas.get("bounds"), Mapping) else {}
        spatial_map = _canvas_map(legend=legend, bounds=bounds)
        canvas_name = str(canvas.get("canvas_name") or DEFAULT_CANVAS_NAME)
        canvas_id = str(canvas.get("canvas_id") or "")
        revision = int(canvas.get("revision") or 0)
        canvas_uri = self.canvas_uri(canvas_name=canvas_name, revision=revision)
        lines = [
            "[CANVAS BOARD]",
            f"canvas_name: {canvas_name}",
            f"canvas_id: {canvas_id}",
            f"canvas_uri: {canvas_uri}",
            f"revision: {revision}",
            f"bounds: x={bounds.get('x')} y={bounds.get('y')} w={bounds.get('w')} h={bounds.get('h')}",
            f"cards: {len(active_legend)} placed={len(placed)} floating={len(floating)} pending_suggestions={len(suggested)} bin={len(legend) - len(active_legend)}",
            "",
            "spatial_map:",
            *spatial_map,
            "",
            "legend:",
            *_legend_text_lines(legend),
            "",
            "edit_protocol:",
            "- Use canvas.patch with canvas_id and base_revision equal to revision.",
            "- Treat this JSON as exact state for planning only; do not edit or save it directly.",
            "- Use map labels for spatial reasoning; use card_id values from the legend when patching existing cards.",
            "- Use card refs only when content_preview is missing or insufficient; ext:/fi: refs are pull/readable, while mem:/so:/task: refs use subsystem tools/resolvers.",
            "- user.text card content can be updated with update_card content={text}. Proxy cards keep their ref content unchanged; use description/comment_card for user notes on them.",
            "- Card kind describes content. Suggestion is placement/state: use placement=suggested for pending bot output.",
            "- Use kind=agent.text only for assistant-authored text; files, memories, sources, search results, and links keep their own kinds.",
            "- Every canvas.patch creates a new revision and event.canvas timeline result.",
            "- Do not edit read-only snapshot refs; canvas cards are editable pins, snapshots are context.",
        ]
        return {
            "schema": "kdcube.canvas.projection.v1",
            "canvas_id": canvas_id,
            "canvas_name": canvas_name,
            "canvas_uri": canvas_uri,
            "owner_user_id": canvas.get("owner_user_id"),
            "story_id": canvas.get("story_id"),
            "revision": revision,
            "bounds": bounds,
            "cards_count": len(legend),
            "active_cards_count": len(active_legend),
            "bin_count": len(legend) - len(active_legend),
            "placed_count": len(placed),
            "floating_count": len(floating),
            "suggested_count": len(suggested),
            "spatial_map": spatial_map,
            "legend": legend,
            "text": "\n".join(lines),
        }

    def _manifest_update(
        self,
        *,
        canvas_id: str,
        story_id: str,
        canvas_name: str,
        canvas: Mapping[str, Any],
        canvas_ref: str,
    ) -> None:
        relpath = self.manifest_relpath()
        _, manifest = self._read_json(relpath)
        canvases = manifest.get("canvases") if isinstance(manifest.get("canvases"), dict) else {}
        now = int(time.time())
        canvases[canvas_id] = {
            "canvas_name": self.canvas_name(canvas_name),
            "canvas_id": canvas_id,
            "owner_user_id": self.user_id,
            "story_id": story_id,
            "latest_revision": int(canvas.get("revision") or 0),
            "canvas_ref": canvas_ref,
            "updated_at": now,
        }
        document = {
            "schema": "kdcube.canvas.manifest.v1",
            "owner_user_id": self.user_id,
            "updated_at": now,
            "canvases": canvases,
        }
        self.artifacts.write(
            relpath,
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            mime="application/json",
            meta={"story_id": story_id, "origin": f"{self.origin_prefix}.manifest"},
        )

    def write_document(
        self,
        *,
        canvas: Mapping[str, Any],
        canvas_id: str,
        story_id: str,
        canvas_name: str,
    ) -> Dict[str, Any]:
        doc = self.normalize_document(canvas, canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
        rel_latest = self.latest_relpath(canvas_id=canvas_id)
        rel_revision = self.revision_relpath(canvas_id=canvas_id, revision=int(doc.get("revision") or 0))
        body = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
        meta = {
            "story_id": story_id,
            "owner_user_id": self.user_id,
            "canvas_name": self.canvas_name(canvas_name),
            "canvas_id": canvas_id,
            "revision": int(doc.get("revision") or 0),
            "origin": self.origin_prefix,
        }
        revision_uri = self.artifacts.write(rel_revision, body, mime=CANVAS_MIME, meta=meta)
        latest_uri = self.artifacts.write(rel_latest, body, mime=CANVAS_MIME, meta={**meta, "latest": True})
        canvas_ref = f"ext:{rel_revision}"
        self._manifest_update(
            canvas_id=canvas_id,
            story_id=story_id,
            canvas_name=canvas_name,
            canvas=doc,
            canvas_ref=canvas_ref,
        )
        self.prune_revisions(canvas_id=canvas_id, keep=self.revision_retention)
        LOGGER.info(
            "[canvas.revision] created user_id=%s story_id=%s canvas_id=%s canvas_name=%s revision=%s canvas_ref=%s latest_ref=%s",
            self.user_id,
            story_id,
            canvas_id,
            self.canvas_name(canvas_name),
            int(doc.get("revision") or 0),
            canvas_ref,
            f"ext:{rel_latest}",
        )
        return {
            "canvas": doc,
            "canvas_ref": canvas_ref,
            "latest_ref": f"ext:{rel_latest}",
            "storage_uri": revision_uri,
            "latest_storage_uri": latest_uri,
            "key": rel_revision,
            "latest_key": rel_latest,
        }

    def prune_revisions(self, *, canvas_id: str, keep: int | None = None) -> int:
        """Best-effort retention for immutable canvas revision documents.

        Only canvas JSON revision files are pruned. Card object bytes are never
        pruned here because current or historical cards may still point at
        their versioned `ext:` object refs.
        """

        keep_count = max(1, int(keep if keep is not None else self.revision_retention))
        prefix = (self.base_relpath(canvas_id=canvas_id) / "revisions").as_posix()
        try:
            names = list(self.artifacts.list(prefix))
        except Exception:
            LOGGER.debug("[canvas.retention] unable to list revisions canvas_id=%s", canvas_id, exc_info=True)
            return 0
        revisions: list[tuple[int, str]] = []
        for name in names:
            text = str(name or "").strip().rsplit("/", 1)[-1]
            if not text.endswith(".json"):
                continue
            try:
                rev = int(text[:-5])
            except Exception:
                continue
            revisions.append((rev, text))
        if len(revisions) <= keep_count:
            return 0
        revisions.sort(key=lambda row: row[0])
        doomed = revisions[: max(0, len(revisions) - keep_count)]
        deleted = 0
        for _, filename in doomed:
            key = f"{prefix}/{filename}"
            try:
                deleted += int(self.artifacts.delete(key) or 0)
            except Exception:
                LOGGER.debug("[canvas.retention] unable to delete revision key=%s", key, exc_info=True)
        if deleted:
            LOGGER.info(
                "[canvas.retention] pruned canvas_id=%s deleted=%s kept=%s",
                canvas_id,
                deleted,
                keep_count,
            )
        return deleted

    def list_canvases(self, *, story_id: str = "") -> Dict[str, Any]:
        _, manifest = self._read_json(self.manifest_relpath())
        canvases = manifest.get("canvases") if isinstance(manifest.get("canvases"), Mapping) else {}
        items = [dict(item) for item in canvases.values() if isinstance(item, Mapping)]
        items.sort(key=lambda item: (str(item.get("canvas_name") or ""), int(item.get("latest_revision") or 0)))
        return {"ok": True, "user_id": self.user_id, "story_id": story_id, "canvases": items}

    def read(
        self,
        *,
        story_id: str,
        canvas_name: str,
        canvas_id: str,
        revision: int | None = None,
    ) -> Dict[str, Any]:
        found, canvas = self.read_document(
            canvas_id=canvas_id,
            story_id=story_id,
            canvas_name=canvas_name,
            revision=revision,
        )
        canvas_ref = (
            f"ext:{self.revision_relpath(canvas_id=canvas_id, revision=int(canvas.get('revision') or 0))}"
            if found
            else ""
        )
        latest_ref = f"ext:{self.latest_relpath(canvas_id=canvas_id)}"
        projection = self.projection(canvas)
        return {
            "ok": True,
            "found": found,
            "user_id": self.user_id,
            "story_id": story_id,
            "canvas_name": canvas_name,
            "canvas_id": canvas.get("canvas_id"),
            "revision": int(canvas.get("revision") or 0),
            "canvas_ref": canvas_ref,
            "latest_ref": latest_ref if found else "",
            "canvas": canvas,
            "projection": projection,
            "agent_view": projection.get("text") or "",
            "canvas_uri": projection.get("canvas_uri") or self.canvas_uri(canvas_name=canvas_name, revision=int(canvas.get("revision") or 0)),
        }

    def read_uri(
        self,
        *,
        uri: str,
        story_id: str = "",
        canvas_name: str = DEFAULT_CANVAS_NAME,
        canvas_id: str = "",
    ) -> Dict[str, Any]:
        parsed = parse_canvas_uri(uri)
        scheme = str(parsed.get("scheme") or "")
        if scheme == "canvas":
            name = self.canvas_name(parsed.get("canvas_name") or canvas_name)
            cid = self.canvas_id(canvas_name=name, canvas_id=canvas_id)
            return self.read(
                story_id=story_id,
                canvas_name=name,
                canvas_id=cid,
                revision=parsed.get("revision"),
            )
        if scheme == "ext":
            key = str(parsed.get("key") or "").strip()
            found, data = self._read_json(key)
            if not found:
                return {
                    "ok": True,
                    "found": False,
                    "uri": str(uri or "").strip(),
                    "error": "canvas_ref_not_found",
                }
            name = self.canvas_name(data.get("canvas_name") or canvas_name)
            cid = str(data.get("canvas_id") or self.canvas_id(canvas_name=name, canvas_id=canvas_id))
            canvas = self.normalize_document(data, canvas_id=cid, story_id=story_id, canvas_name=name)
            projection = self.projection(canvas)
            return {
                "ok": True,
                "found": True,
                "uri": str(uri or "").strip(),
                "user_id": self.user_id,
                "story_id": story_id or str(canvas.get("story_id") or ""),
                "canvas_name": name,
                "canvas_id": canvas.get("canvas_id"),
                "revision": int(canvas.get("revision") or 0),
                "canvas_ref": str(uri or "").strip(),
                "latest_ref": f"ext:{self.latest_relpath(canvas_id=cid)}",
                "canvas": canvas,
                "projection": projection,
                "agent_view": projection.get("text") or "",
                "canvas_uri": projection.get("canvas_uri") or self.canvas_uri(canvas_name=name, revision=int(canvas.get("revision") or 0)),
            }
        return {
            "ok": False,
            "found": False,
            "uri": str(uri or "").strip(),
            "error": "unsupported_canvas_uri",
        }

    def write(
        self,
        *,
        story_id: str,
        canvas_name: str,
        canvas_id: str,
        canvas_input: Mapping[str, Any],
        base_revision: Any = None,
    ) -> Dict[str, Any]:
        with observed_file_lock(
            lock_path=self.lock_path(canvas_id=canvas_id),
            resource_id=f"{self.bundle_id}:canvas:{self.user_id}:{canvas_id}",
            operation=f"{self.origin_prefix}.write",
            wait_seconds=10,
        ):
            found, current = self.read_document(canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
            if base_revision is not None:
                expected_revision = int(base_revision)
                if expected_revision != int(current.get("revision") or 0):
                    current_projection = self.projection(current)
                    LOGGER.warning(
                        "[canvas.write.conflict] user_id=%s story_id=%s canvas_id=%s canvas_name=%s expected_revision=%s current_revision=%s",
                        self.user_id,
                        story_id,
                        canvas_id,
                        canvas_name,
                        expected_revision,
                        int(current.get("revision") or 0),
                    )
                    return {
                        "ok": False,
                        "error": "canvas_revision_conflict",
                        "user_id": self.user_id,
                        "story_id": story_id,
                        "canvas_id": canvas_id,
                        "canvas_name": canvas_name,
                        "expected_revision": expected_revision,
                        "current_revision": int(current.get("revision") or 0),
                    }
            doc = self.normalize_document(canvas_input, canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
            doc["cards"] = [
                self.normalize_card(canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name, raw=card)
                for card in doc.get("cards") or []
                if isinstance(card, Mapping)
            ]
            doc["revision"] = int(current.get("revision") or 0) + 1 if found else max(1, int(doc.get("revision") or 1))
            doc["updated_at"] = int(time.time())
            result = self.write_document(canvas=doc, canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
        projection = self.projection(result["canvas"])
        return {
            "ok": True,
            **result,
            "projection": projection,
            "agent_view": projection.get("text") or "",
            "canvas_uri": projection.get("canvas_uri") or "",
        }

    def apply_patch(
        self,
        *,
        canvas: Mapping[str, Any],
        patch: Mapping[str, Any],
        canvas_id: str,
        story_id: str,
        canvas_name: str,
        actor: str,
    ) -> Dict[str, Any]:
        doc = self.normalize_document(canvas, canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
        operations = patch.get("operations") if isinstance(patch.get("operations"), list) else []
        if not operations and patch.get("op"):
            operations = [patch]
        if not operations:
            raise ValueError("canvas patch requires at least one operation")
        cards = [dict(card) for card in doc.get("cards") or [] if isinstance(card, Mapping)]
        changed: list[Dict[str, Any]] = []

        def card_index(card_id: str) -> int:
            for idx, card in enumerate(cards):
                if str(card.get("id") or "") == card_id:
                    return idx
            return -1

        def identity_index(identity_ref: str) -> int:
            if not identity_ref:
                return -1
            for idx, card in enumerate(cards):
                if _card_identity_ref(card) == identity_ref:
                    return idx
            return -1

        for raw_op in operations:
            if not isinstance(raw_op, Mapping):
                continue
            op = str(raw_op.get("op") or "").strip()
            if op == "new_card":
                raw_card = raw_op.get("card") if isinstance(raw_op.get("card"), Mapping) else raw_op
                card = self.normalize_card(
                    canvas_id=canvas_id,
                    story_id=story_id,
                    canvas_name=canvas_name,
                    raw={k: v for k, v in dict(raw_card).items() if k not in {"op", "base_revision"}},
                )
                identity_ref = _card_identity_ref(card)
                existing_idx = identity_index(identity_ref)
                if existing_idx >= 0:
                    existing = dict(cards[existing_idx])
                    attempted_card_id = str(card.get("id") or "")
                    if bool(existing.get("trashed")) or str(existing.get("placement") or "") == "trashed":
                        restored_placement = str(card.get("placement") or "placed")
                        if restored_placement == "trashed":
                            restored_placement = "placed"
                        restored = {
                            **existing,
                            "rect": card.get("rect") or existing.get("rect"),
                            "placement": restored_placement,
                            "trashed": False,
                            "updated_at": int(time.time()),
                        }
                        cards[existing_idx] = restored
                        changed.append({
                            "op": "restore_existing_card",
                            "card_id": existing.get("id"),
                            "attempted_card_id": attempted_card_id,
                            "identity_ref": identity_ref,
                        })
                        LOGGER.info(
                            "[canvas.pin.restore] user_id=%s story_id=%s canvas_id=%s canvas_name=%s identity_ref=%s existing_card_id=%s attempted_card_id=%s",
                            self.user_id,
                            story_id,
                            canvas_id,
                            canvas_name,
                            identity_ref,
                            existing.get("id"),
                            attempted_card_id,
                        )
                        continue
                    LOGGER.info(
                        "[canvas.pin.noop] user_id=%s story_id=%s canvas_id=%s canvas_name=%s identity_ref=%s existing_card_id=%s attempted_card_id=%s",
                        self.user_id,
                        story_id,
                        canvas_id,
                        canvas_name,
                        identity_ref,
                        existing.get("id"),
                        attempted_card_id,
                    )
                    continue
                if card_index(str(card.get("id") or "")) >= 0:
                    raise ValueError(f"canvas card already exists: {card.get('id')}")
                cards.append(card)
                changed.append({"op": op, "card_id": card.get("id")})
                continue

            card_id = str(raw_op.get("card_id") or raw_op.get("target_card_id") or "").strip()
            if not card_id:
                raise ValueError(f"{op or 'canvas op'} requires card_id")
            idx = card_index(card_id)
            if idx < 0:
                updates = raw_op.get("set") if isinstance(raw_op.get("set"), Mapping) else {}
                idempotent_missing = (
                    op == "delete_card"
                    or (
                        op == "update_card"
                        and (
                            bool(updates.get("trashed"))
                            or str(updates.get("placement") or "") == "trashed"
                        )
                    )
                )
                if idempotent_missing:
                    LOGGER.info(
                        "[canvas.patch.missing_noop] user_id=%s story_id=%s canvas_id=%s canvas_name=%s op=%s card_id=%s",
                        self.user_id,
                        story_id,
                        canvas_id,
                        canvas_name,
                        op,
                        card_id,
                    )
                    continue
                raise ValueError(f"canvas card not found: {card_id}")
            card = dict(cards[idx])

            if op == "update_card":
                updates = raw_op.get("set") if isinstance(raw_op.get("set"), Mapping) else {}
                for key, value in dict(updates).items():
                    if key not in {"id", "created_at"}:
                        card[key] = value
                if "content" in raw_op:
                    card["content"] = raw_op.get("content")
                card = self.normalize_card(canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name, raw=card)
                cards[idx] = card
            elif op == "move_card":
                rect = card.get("rect") if isinstance(card.get("rect"), Mapping) else {}
                card["rect"] = {
                    "x": float(raw_op.get("x", rect.get("x") or 0)),
                    "y": float(raw_op.get("y", rect.get("y") or 0)),
                    "w": float(rect.get("w") or raw_op.get("w") or 240),
                    "h": float(rect.get("h") or raw_op.get("h") or 160),
                }
                card["placement"] = "placed"
                card["updated_at"] = int(time.time())
                cards[idx] = card
            elif op == "resize_card":
                rect = card.get("rect") if isinstance(card.get("rect"), Mapping) else {}
                card["rect"] = {
                    "x": float(rect.get("x") or raw_op.get("x") or 0),
                    "y": float(rect.get("y") or raw_op.get("y") or 0),
                    "w": float(raw_op.get("w", rect.get("w") or 240)),
                    "h": float(raw_op.get("h", rect.get("h") or 160)),
                }
                card["placement"] = "placed"
                card["updated_at"] = int(time.time())
                cards[idx] = card
            elif op == "replace_card":
                replacement = raw_op.get("card") if isinstance(raw_op.get("card"), Mapping) else {}
                mode = str(raw_op.get("mode") or "suggested").strip()
                if mode == "in_place":
                    merged = {**card, **dict(replacement), "id": card_id}
                    cards[idx] = self.normalize_card(
                        canvas_id=canvas_id,
                        story_id=story_id,
                        canvas_name=canvas_name,
                        raw=merged,
                    )
                else:
                    new_card = self.normalize_card(
                        canvas_id=canvas_id,
                        story_id=story_id,
                        canvas_name=canvas_name,
                        raw={
                            **dict(replacement),
                            "id": replacement.get("id") or _new_card_id(replacement.get("kind") or card.get("kind") or "note"),
                            "placement": replacement.get("placement") or "floating",
                            "source_card_ids": [card_id],
                            "kind": replacement.get("kind") or card.get("kind") or "note",
                        },
                    )
                    cards.append(new_card)
                    changed.append({"op": op, "card_id": card_id, "created_card_id": new_card.get("id")})
                    continue
            elif op == "suggest_deletion":
                suggestions = card.get("suggestions") if isinstance(card.get("suggestions"), list) else []
                suggestions.append({
                    "type": "deletion",
                    "reason": str(raw_op.get("reason") or ""),
                    "actor": actor,
                    "created_at": int(time.time()),
                })
                card["suggestions"] = suggestions
                card["updated_at"] = int(time.time())
                cards[idx] = card
            elif op == "delete_card":
                cards.pop(idx)
            elif op == "comment_card":
                comments = card.get("comments") if isinstance(card.get("comments"), list) else []
                comments.append({
                    "id": str(raw_op.get("comment_id") or timestamp_id("comment")),
                    "text": str(raw_op.get("text") or ""),
                    "actor": actor,
                    "created_at": int(time.time()),
                })
                card["comments"] = comments
                card["updated_at"] = int(time.time())
                cards[idx] = card
            else:
                raise ValueError(f"Unsupported canvas patch op: {op}")
            changed.append({"op": op, "card_id": card_id})

        doc["cards"] = cards
        if not changed:
            return doc
        now = int(time.time())
        doc["revision"] = int(doc.get("revision") or 0) + 1
        doc["updated_at"] = now
        history = doc.get("history") if isinstance(doc.get("history"), list) else []
        history.append({
            "schema": CANVAS_PATCH_SCHEMA,
            "revision": doc["revision"],
            "actor": actor,
            "created_at": now,
            "changed": changed,
            "reason": str(patch.get("reason") or ""),
        })
        doc["history"] = history[-100:]
        return doc

    def patch(
        self,
        *,
        story_id: str,
        canvas_name: str,
        canvas_id: str,
        patch: Mapping[str, Any],
        actor: str,
    ) -> Dict[str, Any]:
        with observed_file_lock(
            lock_path=self.lock_path(canvas_id=canvas_id),
            resource_id=f"{self.bundle_id}:canvas:{self.user_id}:{canvas_id}",
            operation=f"{self.origin_prefix}.patch",
            wait_seconds=10,
        ):
            _, current = self.read_document(canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
            base_revision = patch.get("base_revision") if isinstance(patch, Mapping) else None
            operations = patch.get("operations") if isinstance(patch, Mapping) and isinstance(patch.get("operations"), list) else []
            if not operations and isinstance(patch, Mapping) and patch.get("op"):
                operations = [patch]
            LOGGER.info(
                "[canvas.patch] start user_id=%s story_id=%s canvas_id=%s canvas_name=%s base_revision=%s current_revision=%s actor=%s ops=%s",
                self.user_id,
                story_id,
                canvas_id,
                canvas_name,
                base_revision,
                int(current.get("revision") or 0),
                actor,
                [str(op.get("op") or "") for op in operations if isinstance(op, Mapping)],
            )
            if base_revision is not None:
                expected_revision = int(base_revision)
                if expected_revision != int(current.get("revision") or 0):
                    current_projection = self.projection(current)
                    LOGGER.warning(
                        "[canvas.patch.conflict] user_id=%s story_id=%s canvas_id=%s canvas_name=%s expected_revision=%s current_revision=%s actor=%s ops=%s",
                        self.user_id,
                        story_id,
                        canvas_id,
                        canvas_name,
                        expected_revision,
                        int(current.get("revision") or 0),
                        actor,
                        [str(op.get("op") or "") for op in operations if isinstance(op, Mapping)],
                    )
                    return {
                        "ok": False,
                        "error": "canvas_revision_conflict",
                        "user_id": self.user_id,
                        "story_id": story_id,
                        "canvas_id": canvas_id,
                        "canvas_name": canvas_name,
                        "expected_revision": expected_revision,
                        "current_revision": int(current.get("revision") or 0),
                        "canvas": current,
                        "projection": current_projection,
                        "agent_view": current_projection.get("text") or "",
                    }
            next_canvas = self.apply_patch(
                canvas=current,
                patch=patch,
                canvas_id=canvas_id,
                story_id=story_id,
                canvas_name=canvas_name,
                actor=actor,
            )
            if int(next_canvas.get("revision") or 0) == int(current.get("revision") or 0):
                rel_latest = self.latest_relpath(canvas_id=canvas_id)
                rel_revision = self.revision_relpath(canvas_id=canvas_id, revision=int(current.get("revision") or 0))
                result = {
                    "canvas": next_canvas,
                    "canvas_ref": f"ext:{rel_revision}",
                    "latest_ref": f"ext:{rel_latest}",
                    "storage_uri": "",
                    "latest_storage_uri": "",
                    "key": rel_revision,
                    "latest_key": rel_latest,
                    "noop": True,
                }
                LOGGER.info(
                    "[canvas.patch] noop user_id=%s story_id=%s canvas_id=%s canvas_name=%s revision=%s actor=%s",
                    self.user_id,
                    story_id,
                    canvas_id,
                    canvas_name,
                    int(current.get("revision") or 0),
                    actor,
                )
            else:
                result = self.write_document(canvas=next_canvas, canvas_id=canvas_id, story_id=story_id, canvas_name=canvas_name)
        projection = self.projection(result["canvas"])
        changed = [] if result.get("noop") else (
            (result["canvas"].get("history") or [])[-1:] if isinstance(result["canvas"].get("history"), list) else []
        )
        changed_rows = []
        if changed and isinstance(changed[0], Mapping):
            raw_changed = changed[0].get("changed") if isinstance(changed[0].get("changed"), list) else []
            changed_rows = [dict(row) for row in raw_changed if isinstance(row, Mapping)]
        changed_cards = _changed_cards(result["canvas"], changed_rows)
        LOGGER.info(
            "[canvas.patch] applied user_id=%s story_id=%s canvas_id=%s canvas_name=%s revision=%s actor=%s changed=%s",
            self.user_id,
            story_id,
            canvas_id,
            canvas_name,
            int(result["canvas"].get("revision") or 0),
            actor,
            changed_rows,
        )
        return {
            "ok": True,
            **result,
            "changed": changed,
            "changed_cards": changed_cards,
            "noop": bool(result.get("noop")),
            "projection": projection,
            "agent_view": projection.get("text") or "",
            "canvas_uri": projection.get("canvas_uri") or "",
            "ui_event": {
                "type": self.ui_event_type,
                "source": "canvas.patch",
                "story_id": story_id,
                "canvas_name": canvas_name,
                "canvas_id": result["canvas"].get("canvas_id"),
                "revision": int(result["canvas"].get("revision") or 0),
                "canvas_uri": projection.get("canvas_uri") or "",
                "canvas_ref": result["canvas_ref"],
                "latest_ref": result["latest_ref"],
                "changed": changed,
                "changed_cards": changed_cards,
                "projection": projection,
            },
        }

    def state_event(
        self,
        *,
        canvas: Mapping[str, Any],
        canvas_ref: str,
        latest_ref: str,
        agent_id: str,
        surface: str,
    ) -> Dict[str, Any]:
        canvas_name = str(canvas.get("canvas_name") or DEFAULT_CANVAS_NAME)
        revision = int(canvas.get("revision") or 0)
        canvas_uri = self.canvas_uri(canvas_name=canvas_name, revision=revision)
        return {
            "event_id": timestamp_id("evt"),
            "type": "event.canvas",
            "event_source_id": self.state_event_source_id,
            "reactive": False,
            "story_id": str(canvas.get("story_id") or ""),
            "agent_id": agent_id,
            "payload": {
                "mime": CANVAS_MIME,
                "event_ref": canvas_ref,
                "event": {
                    "canvas_id": str(canvas.get("canvas_id") or ""),
                    "canvas_name": canvas_name,
                    "canvas_uri": canvas_uri,
                    "owner_user_id": str(canvas.get("owner_user_id") or ""),
                    "story_id": str(canvas.get("story_id") or ""),
                    "revision": revision,
                    "canvas_ref": canvas_ref,
                    "latest_ref": latest_ref,
                    "surface": surface,
                    "projection": self.projection(canvas),
                },
            },
        }
