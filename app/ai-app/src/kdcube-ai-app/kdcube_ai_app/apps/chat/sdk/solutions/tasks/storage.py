from __future__ import annotations

import re
import json
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

try:
    from .executions_storage import TaskExecutionsStorage
except ImportError:
    import importlib.util
    import sys

    _task_executions_storage_path = Path(__file__).with_name("executions_storage.py")
    _task_executions_storage_spec = importlib.util.spec_from_file_location(
        "_kdcube_tasks_executions_storage",
        _task_executions_storage_path,
    )
    if _task_executions_storage_spec is None or _task_executions_storage_spec.loader is None:
        raise
    _task_executions_storage_mod = importlib.util.module_from_spec(_task_executions_storage_spec)
    sys.modules.setdefault("_kdcube_tasks_executions_storage", _task_executions_storage_mod)
    _task_executions_storage_spec.loader.exec_module(_task_executions_storage_mod)
    TaskExecutionsStorage = _task_executions_storage_mod.TaskExecutionsStorage


TASK_STATUSES = {"enabled", "disabled", "archived", "deleted"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except Exception:
        return None
    return max(0, int((finish - start).total_seconds() * 1000))


def _slug(text: str, *, fallback: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return value[:48].strip("-") or fallback


def _csv(value: str | Iterable[str] | None) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _split_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :]).lstrip()
            data = yaml.safe_load(raw) or {}
            return data if isinstance(data, dict) else {}, body
    return {}, text


def _render_frontmatter(data: Dict[str, Any], body: str) -> str:
    frontmatter = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"


def _json_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return _csv(value)
        return _json_list(parsed)
    return []


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is bool:
        return value
    raise ValueError("recurring must be a boolean")


def _json_dict_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return _json_dict_list(parsed)
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _normalize_task_schedule(asset: Dict[str, Any]) -> None:
    schedule = asset.get("schedule") if isinstance(asset.get("schedule"), dict) else {}
    schedule["cron"] = str(schedule.get("cron") or "").strip()
    schedule["timezone"] = str(schedule.get("timezone") or "UTC").strip() or "UTC"
    schedule["recurring"] = _bool_value(schedule.get("recurring"), default=True)
    asset["schedule"] = schedule


def _fts_terms(text: str) -> List[str]:
    terms: List[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9]+", str(text or "").lower()):
        term = raw.strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _fts_query(text: str) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    clauses: List[str] = []
    phrase = raw.replace('"', " ").strip()
    if phrase:
        clauses.append(f'"{phrase}"')
    for term in _fts_terms(raw):
        clauses.append(f"{term}*" if len(term) >= 3 else term)
    return " OR ".join(_dedupe(clauses)) or None


def _filter_markdown_assets(
    paths: Iterable[Path],
    *,
    root: Path,
    status: str = "",
    query: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    query_norm = (query or "").strip().lower()
    status_norm = (status or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        meta["body"] = body
        meta["description"] = body
        meta["path"] = str(path.relative_to(root))
        _normalize_task_schedule(meta)
        if status_norm and str(meta.get("status") or "").lower() != status_norm:
            continue
        haystack = "\n".join(
            [
                str(meta.get("id") or ""),
                str(meta.get("title") or ""),
                str(meta.get("body") or ""),
                str(meta.get("metadata") or ""),
                str(meta.get("traits") or ""),
            ]
        ).lower()
        if query_norm and query_norm not in haystack:
            continue
        rows.append(meta)
        if len(rows) >= max(1, int(limit or 50)):
            break
    return rows


def _safe_segment(raw: str, *, fallback: str = "default") -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw or "").strip("-")
    return value or fallback


def _memory_access_policy(visibility: str) -> Dict[str, Any]:
    normalized = (visibility or "user").strip().lower()
    visible_to_user = normalized in {"user", "owner", "public"}
    return {
        "id": "user_visible_memory" if visible_to_user else "internal_memory",
        "visible_to_user": visible_to_user,
        "scope": "owner" if visible_to_user else "agent_internal",
        "visibility": normalized,
    }


def _is_user_visible(asset: Dict[str, Any]) -> bool:
    policy = asset.get("access_policy") if isinstance(asset.get("access_policy"), dict) else {}
    if "visible_to_user" in policy:
        return bool(policy.get("visible_to_user"))
    visibility = str(asset.get("visibility") or "user").strip().lower()
    return visibility in {"user", "owner", "public"}


class TaskStorage:
    """Markdown plus YAML-front-matter storage for executable task assets."""

    SCHEMA_VERSION = "task-index.v2"

    def __init__(self, root: str | Path, *, user_id: str):
        self.root = Path(root).resolve()
        self.user_id = user_id or "anonymous"
        self.safe_user_id = _safe_segment(self.user_id, fallback="anonymous")
        self.tasks_dir = self.root / "tasks" / self.safe_user_id
        self.index_dir = self.root / "indexes" / "tasks" / self.safe_user_id
        self.index_path = self.index_dir / "tasks.sqlite"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.executions = TaskExecutionsStorage(
            self.root,
            user_id=self.user_id,
            task_loader=self.get_task,
            task_summary_updater=self._touch_task_execution_summary,
        )

    def _path(self, asset_id: str) -> Path:
        safe = _safe_segment(asset_id)
        if not safe:
            raise ValueError("asset id is required")
        return self.tasks_dir / f"{safe}.md"

    def _read_asset(self, path: Path) -> Dict[str, Any]:
        meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        meta["body"] = body
        meta["description"] = body
        meta["path"] = str(path.relative_to(self.root))
        _normalize_task_schedule(meta)
        return meta

    def _write_asset(self, data: Dict[str, Any], body: str) -> Dict[str, Any]:
        payload = dict(data)
        payload.pop("path", None)
        payload.pop("body", None)
        payload.pop("description", None)
        path = self._path(str(payload["id"]))
        path.write_text(_render_frontmatter(payload, body), encoding="utf-8")
        asset = self._read_asset(path)
        try:
            self.rebuild_search_index()
        except Exception:
            pass
        return asset

    def _signature(self) -> str:
        rows: List[str] = []
        for path in sorted(self.tasks_dir.glob("*.md")):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            rel = path.relative_to(self.root).as_posix()
            rows.append(f"{rel}\t{int(stat.st_mtime_ns)}\t{int(stat.st_size)}")
        import hashlib

        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()

    def _open_index(self) -> sqlite3.Connection:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.index_path))
        conn.row_factory = sqlite3.Row
        return conn

    def rebuild_search_index(self) -> Path:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.index_path, self.index_path.with_name(self.index_path.name + "-wal"), self.index_path.with_name(self.index_path.name + "-shm")):
            if path.exists():
                path.unlink()

        conn = self._open_index()
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE tasks (
                docid INTEGER PRIMARY KEY,
                id TEXT NOT NULL UNIQUE,
                owner_user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                schedule_cron TEXT,
                schedule_timezone TEXT,
                schedule_recurring INTEGER NOT NULL DEFAULT 1,
                conversation_id TEXT,
                execution_conversation_id TEXT,
                path TEXT NOT NULL,
                body TEXT,
                labels_json TEXT NOT NULL,
                relations_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE tasks_fts USING fts5(
                id,
                title,
                body,
                labels,
                relations,
                conversation_id,
                tokenize = 'unicode61'
            );
            """
        )

        docid = 0
        for path in sorted(self.tasks_dir.glob("*.md")):
            asset = self._read_asset(path)
            docid += 1
            metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
            labels = _json_list(metadata.get("labels"))
            relations = asset.get("relations") if isinstance(asset.get("relations"), dict) else {}
            relation_terms: List[str] = []
            for value in relations.values():
                if isinstance(value, list):
                    relation_terms.extend(_json_list(value))
                elif value:
                    relation_terms.append(str(value))
            schedule = asset.get("schedule") if isinstance(asset.get("schedule"), dict) else {}
            execution = asset.get("execution") if isinstance(asset.get("execution"), dict) else {}
            conversation_id = str(asset.get("conversation_id") or "").strip()
            execution_conversation_id = str(execution.get("conversation_id") or conversation_id or "").strip()
            conn.execute(
                """
                INSERT INTO tasks (
                    docid, id, owner_user_id, title, status, created_at, updated_at,
                    schedule_cron, schedule_timezone, schedule_recurring, conversation_id,
                    execution_conversation_id, path, body, labels_json,
                    relations_json, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    docid,
                    str(asset.get("id") or ""),
                    str(asset.get("owner_user_id") or self.user_id),
                    str(asset.get("title") or ""),
                    str(asset.get("status") or ""),
                    asset.get("created_at"),
                    asset.get("updated_at"),
                    schedule.get("cron"),
                    schedule.get("timezone"),
                    1 if _bool_value(schedule.get("recurring"), default=True) else 0,
                    conversation_id,
                    execution_conversation_id,
                    str(asset.get("path") or ""),
                    str(asset.get("body") or ""),
                    json.dumps(labels),
                    json.dumps(relations),
                    json.dumps(metadata),
                ),
            )
            conn.execute(
                """
                INSERT INTO tasks_fts (
                    rowid, id, title, body, labels, relations, conversation_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    docid,
                    str(asset.get("id") or ""),
                    str(asset.get("title") or ""),
                    str(asset.get("body") or ""),
                    " ".join(labels),
                    " ".join(relation_terms),
                    " ".join([conversation_id, execution_conversation_id]),
                ),
            )
        conn.execute("INSERT INTO meta (key, value) VALUES ('signature', ?)", (self._signature(),))
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (self.SCHEMA_VERSION,))
        conn.commit()
        conn.close()
        return self.index_path

    def ensure_search_index(self) -> Path:
        signature = self._signature()
        if self.index_path.exists():
            try:
                conn = self._open_index()
                current = conn.execute("SELECT value FROM meta WHERE key = 'signature'").fetchone()
                schema = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                conn.close()
                if current and schema and current["value"] == signature and schema["value"] == self.SCHEMA_VERSION:
                    return self.index_path
            except Exception:
                pass
        return self.rebuild_search_index()

    def _decode_task_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        task = {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "owner_user_id": row["owner_user_id"],
            "schedule": {
                "cron": row["schedule_cron"] or "",
                "timezone": row["schedule_timezone"] or "UTC",
                "recurring": bool(row["schedule_recurring"]),
            },
            "conversation_id": row["conversation_id"] or None,
            "execution": {
                "conversation_id": row["execution_conversation_id"] or row["conversation_id"] or None,
            },
            "path": row["path"],
            "body": row["body"] or "",
            "description": row["body"] or "",
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "relations": json.loads(row["relations_json"] or "{}"),
        }
        return task

    def create_task(
        self,
        *,
        title: str,
        description: str = "",
        schedule_cron: str = "",
        timezone_name: str = "UTC",
        recurring: bool = True,
        labels: str | Iterable[str] | None = None,
        source: str = "agent",
        conversation_id: str | None = None,
    ) -> Dict[str, Any]:
        now = _utc_now()
        asset_id = f"task_{_slug(title, fallback='task')}_{uuid.uuid4().hex[:8]}"
        body = description or title
        data = {
            "id": asset_id,
            "title": title.strip() or "Untitled task",
            "status": "enabled",
            "created_at": now,
            "updated_at": now,
            "owner_user_id": self.user_id,
            "created_by": self.user_id,
            "source": source or "agent",
            "schedule": {
                "cron": (schedule_cron or "").strip(),
                "timezone": (timezone_name or "UTC").strip(),
                "recurring": _bool_value(recurring, default=True),
            },
            "context": {
                "attachments": [],
                "links": [],
                "notes": [],
            },
            "metadata": {
                "labels": _csv(labels),
                "traits": {},
                "related_tasks": [],
            },
            "relations": {
                "parent_task_id": None,
                "child_task_ids": [],
                "depends_on_task_ids": [],
                "blocks_task_ids": [],
                "related_task_ids": [],
            },
            "conversation_id": conversation_id,
            "execution": {
                "conversation_id": conversation_id,
                "last_turn_id": None,
                "last_run_at": None,
            },
        }
        return self._write_asset(data, body)

    def list_tasks(self, *, status: str = "", query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        if query:
            return self.search_tasks(query=query, status=status, limit=limit)
        return _filter_markdown_assets(
            self.tasks_dir.glob("*.md"),
            root=self.root,
            status=status,
            query=query,
            limit=limit,
        )

    def search_tasks(self, *, query: str = "", status: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        try:
            self.ensure_search_index()
            status_norm = (status or "").strip().lower()
            fts = _fts_query(query)
            where: List[str] = []
            params: List[Any] = []
            if fts:
                where.append("tasks_fts MATCH ?")
                params.append(fts)
                from_clause = "FROM tasks_fts JOIN tasks t ON t.docid = tasks_fts.rowid"
                rank_expr = "bm25(tasks_fts, 4.0, 8.0, 2.0, 2.0, 1.0, 1.0)"
                order_by = "rank ASC, t.updated_at DESC, t.title ASC"
            else:
                from_clause = "FROM tasks t"
                rank_expr = "0.0"
                order_by = "t.updated_at DESC, t.title ASC"
            if status_norm:
                where.append("LOWER(t.status) = ?")
                params.append(status_norm)
            sql = f"""
                SELECT
                    t.id, t.owner_user_id, t.title, t.status, t.created_at, t.updated_at,
                    t.schedule_cron, t.schedule_timezone, t.schedule_recurring, t.conversation_id,
                    t.execution_conversation_id, t.path, t.body, t.labels_json,
                    t.relations_json, t.metadata_json, {rank_expr} AS rank
                {from_clause}
                {"WHERE " + " AND ".join(where) if where else ""}
                ORDER BY {order_by}
                LIMIT ?
            """
            params.append(max(1, int(limit or 50)))
            conn = self._open_index()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            results = []
            for row in rows:
                task = self._decode_task_row(row)
                task["score"] = round(-float(row["rank"] or 0.0), 3)
                results.append(task)
            return results
        except Exception:
            return _filter_markdown_assets(
                self.tasks_dir.glob("*.md"),
                root=self.root,
                status=status,
                query=query,
                limit=limit,
            )

    def get_task(self, task_id: str) -> Dict[str, Any] | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        return self._read_asset(path)

    def set_task_status(self, *, task_id: str, status: str) -> Dict[str, Any]:
        normalized = (status or "").strip().lower()
        if normalized not in TASK_STATUSES:
            raise ValueError("status must be enabled, disabled, archived, or deleted")
        path = self._path(task_id)
        asset = self._read_asset(path)
        body = str(asset.pop("body", "") or "")
        asset.pop("path", None)
        asset["status"] = normalized
        asset["updated_at"] = _utc_now()
        return self._write_asset(asset, body)

    def link_task(
        self,
        *,
        task_id: str,
        target_task_id: str,
        relation: str = "related",
        reciprocal: bool = True,
    ) -> Dict[str, Any]:
        normalized = (relation or "related").strip().lower()
        if normalized not in {"related", "child", "depends_on", "blocks"}:
            raise ValueError("relation must be related, child, depends_on, or blocks")
        if task_id == target_task_id:
            raise ValueError("task cannot be related to itself")

        path = self._path(task_id)
        target_path = self._path(target_task_id)
        if not path.exists():
            raise ValueError(f"task {task_id!r} was not found")
        if not target_path.exists():
            raise ValueError(f"target task {target_task_id!r} was not found")

        asset = self._read_asset(path)
        target = self._read_asset(target_path)
        body = str(asset.pop("body", "") or "")
        target_body = str(target.pop("body", "") or "")
        asset.pop("path", None)
        target.pop("path", None)

        def ensure_relations(item: Dict[str, Any]) -> Dict[str, Any]:
            relations = item.get("relations") if isinstance(item.get("relations"), dict) else {}
            relations.setdefault("parent_task_id", None)
            relations["child_task_ids"] = _dedupe(_json_list(relations.get("child_task_ids")))
            relations["depends_on_task_ids"] = _dedupe(_json_list(relations.get("depends_on_task_ids")))
            relations["blocks_task_ids"] = _dedupe(_json_list(relations.get("blocks_task_ids")))
            relations["related_task_ids"] = _dedupe(_json_list(relations.get("related_task_ids")))
            item["relations"] = relations
            return relations

        rel = ensure_relations(asset)
        target_rel = ensure_relations(target)
        if normalized == "related":
            rel["related_task_ids"] = _dedupe(rel["related_task_ids"] + [target_task_id])
            if reciprocal:
                target_rel["related_task_ids"] = _dedupe(target_rel["related_task_ids"] + [task_id])
        elif normalized == "child":
            rel["child_task_ids"] = _dedupe(rel["child_task_ids"] + [target_task_id])
            if reciprocal:
                target_rel["parent_task_id"] = task_id
        elif normalized == "depends_on":
            rel["depends_on_task_ids"] = _dedupe(rel["depends_on_task_ids"] + [target_task_id])
            if reciprocal:
                target_rel["blocks_task_ids"] = _dedupe(target_rel["blocks_task_ids"] + [task_id])
        elif normalized == "blocks":
            rel["blocks_task_ids"] = _dedupe(rel["blocks_task_ids"] + [target_task_id])
            if reciprocal:
                target_rel["depends_on_task_ids"] = _dedupe(target_rel["depends_on_task_ids"] + [task_id])

        for item in (asset, target):
            all_related = []
            relations = item.get("relations") or {}
            for key in ("child_task_ids", "depends_on_task_ids", "blocks_task_ids", "related_task_ids"):
                all_related.extend(_json_list(relations.get(key)))
            if relations.get("parent_task_id"):
                all_related.append(str(relations.get("parent_task_id")))
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            metadata["related_tasks"] = _dedupe(all_related)
            item["metadata"] = metadata
            item["updated_at"] = _utc_now()

        updated = self._write_asset(asset, body)
        self._write_asset(target, target_body)
        return updated

    def update_task(
        self,
        *,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        schedule_cron: str | None = None,
        timezone_name: str | None = None,
        recurring: bool | None = None,
        labels: str | Iterable[str] | None = None,
        conversation_id: str | None = None,
        execution_conversation_id: str | None = None,
        metadata_patch: Dict[str, Any] | None = None,
        context_patch: Dict[str, Any] | None = None,
        relations_patch: Dict[str, Any] | None = None,
        revision_mode: str = "auto",
    ) -> Dict[str, Any]:
        path = self._path(task_id)
        if not path.exists():
            raise ValueError(f"task {task_id!r} was not found")
        asset = self._read_asset(path)
        body = str(asset.get("body") or "")
        revision_mode_norm = str(revision_mode or "auto").strip().lower()
        if revision_mode_norm not in {"auto", "in_place", "archive_and_create"}:
            raise ValueError("revision_mode must be auto, in_place, or archive_and_create")
        status_norm = None
        if status is not None:
            status_norm = str(status or "").strip().lower()
            if status_norm not in TASK_STATUSES:
                raise ValueError("status must be enabled, disabled, archived, or deleted")

        semantic_change = any(
            value is not None
            for value in (
                title,
                description,
                schedule_cron,
                timezone_name,
                recurring,
                labels,
                metadata_patch,
                context_patch,
                relations_patch,
            )
        )
        should_create_revision = revision_mode_norm == "archive_and_create" or (
            revision_mode_norm == "auto" and semantic_change
        )
        if should_create_revision:
            now = _utc_now()
            old_asset = json.loads(json.dumps({key: value for key, value in asset.items() if key not in {"path", "body", "description"}}))
            old_body = body

            next_title = str(title or old_asset.get("title") or "Untitled task").strip() or "Untitled task"
            next_body = str(description).strip() if description is not None else old_body
            new_id = f"task_{_slug(next_title, fallback='task')}_{uuid.uuid4().hex[:8]}"

            new_asset = json.loads(json.dumps(old_asset))
            new_asset["id"] = new_id
            new_asset["title"] = next_title
            old_status = str(old_asset.get("status") or "enabled").strip().lower()
            new_asset["status"] = status_norm or (old_status if old_status in {"enabled", "disabled"} else "enabled")
            new_asset["created_at"] = now
            new_asset["updated_at"] = now

            schedule = new_asset.get("schedule") if isinstance(new_asset.get("schedule"), dict) else {}
            if schedule_cron is not None:
                schedule["cron"] = str(schedule_cron or "").strip()
            if timezone_name is not None:
                schedule["timezone"] = str(timezone_name or "UTC").strip() or "UTC"
            if recurring is not None:
                schedule["recurring"] = _bool_value(recurring, default=True)
            new_asset["schedule"] = schedule

            if conversation_id is not None:
                new_asset["conversation_id"] = str(conversation_id or "").strip() or None
            execution = new_asset.get("execution") if isinstance(new_asset.get("execution"), dict) else {}
            execution = {
                "conversation_id": (
                    str(execution_conversation_id or "").strip()
                    or str(new_asset.get("conversation_id") or "").strip()
                    or None
                ),
                "last_turn_id": None,
                "last_run_at": None,
            }
            new_asset["execution"] = execution

            metadata = new_asset.get("metadata") if isinstance(new_asset.get("metadata"), dict) else {}
            if labels is not None:
                metadata["labels"] = _csv(labels)
            if metadata_patch:
                metadata.update(metadata_patch)
            revision = metadata.get("revision") if isinstance(metadata.get("revision"), dict) else {}
            revision.update(
                {
                    "revision_of_task_id": task_id,
                    "revision_reason": "task_definition_updated",
                    "revision_created_at": now,
                }
            )
            metadata["revision"] = revision
            new_asset["metadata"] = metadata

            if context_patch:
                context = new_asset.get("context") if isinstance(new_asset.get("context"), dict) else {}
                context.update(context_patch)
                new_asset["context"] = context
            if relations_patch:
                relations = new_asset.get("relations") if isinstance(new_asset.get("relations"), dict) else {}
                relations.update(relations_patch)
                new_asset["relations"] = relations

            old_metadata = old_asset.get("metadata") if isinstance(old_asset.get("metadata"), dict) else {}
            old_revision = old_metadata.get("revision") if isinstance(old_metadata.get("revision"), dict) else {}
            old_revision.update(
                {
                    "superseded_by_task_id": new_id,
                    "archived_reason": "task_definition_replaced",
                    "archived_at": now,
                }
            )
            old_metadata["revision"] = old_revision
            old_asset["metadata"] = old_metadata
            old_asset["status"] = "archived"
            old_asset["updated_at"] = now

            self._write_asset(old_asset, old_body)
            return self._write_asset(new_asset, next_body)

        if title is not None:
            next_title = str(title or "").strip()
            if next_title:
                asset["title"] = next_title
        if description is not None:
            body = str(description or "").strip()
        if status_norm is not None:
            asset["status"] = status_norm

        schedule = asset.get("schedule") if isinstance(asset.get("schedule"), dict) else {}
        if schedule_cron is not None:
            schedule["cron"] = str(schedule_cron or "").strip()
        if timezone_name is not None:
            schedule["timezone"] = str(timezone_name or "UTC").strip() or "UTC"
        if recurring is not None:
            schedule["recurring"] = _bool_value(recurring, default=True)
        asset["schedule"] = schedule

        if conversation_id is not None:
            asset["conversation_id"] = str(conversation_id or "").strip() or None
        execution = asset.get("execution") if isinstance(asset.get("execution"), dict) else {}
        if execution_conversation_id is not None:
            execution["conversation_id"] = str(execution_conversation_id or "").strip() or asset.get("conversation_id")
        asset["execution"] = execution

        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        if labels is not None:
            metadata["labels"] = _csv(labels)
        if metadata_patch:
            metadata.update(metadata_patch)
        asset["metadata"] = metadata

        if context_patch:
            context = asset.get("context") if isinstance(asset.get("context"), dict) else {}
            context.update(context_patch)
            asset["context"] = context
        if relations_patch:
            relations = asset.get("relations") if isinstance(asset.get("relations"), dict) else {}
            relations.update(relations_patch)
            asset["relations"] = relations

        asset["updated_at"] = _utc_now()
        return self._write_asset(asset, body)

    def delete_task(self, *, task_id: str, hard: bool = False) -> Dict[str, Any] | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        if not hard:
            return self.set_task_status(task_id=task_id, status="deleted")
        deleted = self._read_asset(path)
        path.unlink()
        try:
            self.rebuild_search_index()
        except Exception:
            pass
        return deleted

    def _touch_task_execution_summary(self, task_id: str, execution: Dict[str, Any]) -> None:
        task = self.get_task(task_id)
        if not task:
            return
        body = str(task.get("body") or "")
        task.pop("path", None)
        task.pop("body", None)
        task.pop("description", None)
        task_execution = task.get("execution") if isinstance(task.get("execution"), dict) else {}
        if execution.get("conversation_id"):
            task_execution["conversation_id"] = execution.get("conversation_id")
        if execution.get("turn_id"):
            task_execution["last_turn_id"] = execution.get("turn_id")
        task_execution["last_execution_id"] = execution.get("id")
        task_execution["last_status"] = execution.get("status")
        task_execution["last_run_at"] = (
            execution.get("finished_at")
            or execution.get("started_at")
            or execution.get("updated_at")
            or execution.get("created_at")
        )
        task["execution"] = task_execution
        task["updated_at"] = _utc_now()
        self._write_asset(task, body)

    def create_execution(self, **kwargs) -> Dict[str, Any]:
        return self.executions.create_execution(**kwargs)

    def get_execution(self, *, execution_id: str, task_id: str = "") -> Dict[str, Any] | None:
        return self.executions.get_execution(execution_id=execution_id, task_id=task_id)

    def list_executions(
        self,
        *,
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.executions.list_executions(task_id=task_id, status=status, limit=limit)

    def search_executions(
        self,
        *,
        query: str = "",
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.executions.search_executions(query=query, task_id=task_id, status=status, limit=limit)

    def update_execution(self, **kwargs) -> Dict[str, Any]:
        return self.executions.update_execution(**kwargs)

    def delete_execution(self, *, execution_id: str, task_id: str = "") -> Dict[str, Any] | None:
        return self.executions.delete_execution(execution_id=execution_id, task_id=task_id)

    def attach_execution_history(
        self,
        tasks: Iterable[Dict[str, Any]],
        *,
        execution_limit: int = 3,
    ) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for task in tasks:
            item = dict(task)
            task_id = str(item.get("id") or "").strip()
            executions = self.list_executions(task_id=task_id, limit=execution_limit) if task_id else []
            item["executions"] = executions
            item["execution_count"] = len(self.list_executions(task_id=task_id, limit=100000)) if task_id else 0
            item["last_execution"] = executions[0] if executions else None
            enriched.append(item)
        return enriched

    def rebuild_execution_search_index(self) -> Path:
        return self.executions.rebuild_search_index()

    def ensure_execution_search_index(self) -> Path:
        return self.executions.ensure_search_index()
