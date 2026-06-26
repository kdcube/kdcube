from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List


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


def _safe_segment(raw: str, *, fallback: str = "default") -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw or "").strip("-")
    return value or fallback


def _json_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return [item.strip() for item in value.split(",") if item.strip()]
        return _json_list(parsed)
    return []


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


def _normalize_execution_artifact(item: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now()
    artifact_id = str(item.get("id") or "").strip()
    if not artifact_id:
        seed = str(item.get("filename") or item.get("logical_path") or item.get("stored_path") or "artifact")
        artifact_id = f"art_{_slug(seed, fallback='artifact')}_{uuid.uuid4().hex[:8]}"
    logical_path = str(item.get("logical_path") or item.get("artifact_path") or "").strip()
    source_physical_path = str(
        item.get("source_physical_path") or item.get("physical_path") or item.get("local_path") or ""
    ).strip()
    stored_path = str(item.get("stored_path") or "").strip()
    hosted_uri = str(item.get("hosted_uri") or item.get("url") or item.get("rn") or item.get("key") or "").strip()
    filename = str(item.get("filename") or "").strip()
    if not filename:
        filename = Path(source_physical_path or stored_path or logical_path or hosted_uri).name
    mime_type = str(item.get("mime_type") or item.get("mime") or "").strip()
    visibility = str(item.get("visibility") or "user").strip().lower() or "user"
    normalized = {
        "id": artifact_id,
        "kind": str(item.get("kind") or "file").strip() or "file",
        "logical_path": logical_path,
        "source_physical_path": source_physical_path,
        "stored_path": stored_path,
        "hosted_uri": hosted_uri,
        "mime_type": mime_type,
        "filename": filename,
        "size_bytes": item.get("size_bytes"),
        "visibility": visibility,
        "created_at": str(item.get("created_at") or now).strip(),
        "description": str(item.get("description") or item.get("title") or "").strip(),
    }
    return {key: value for key, value in normalized.items() if value not in ("", None)}


class AutomationExecutionsStorage:
    """File + SQLite storage for automation/subagent execution events.

    The current bundle uses this for automation executions. The class deliberately keeps
    parent-automation coupling behind optional callbacks so the same layout and search
    contract can later back separate subagent/job event streams.
    """

    SCHEMA_VERSION = "automation-execution-index.v1"

    def __init__(
        self,
        root: str | Path,
        *,
        user_id: str,
        namespace: str = "automation_executions",
        automation_loader: Callable[[str], Dict[str, Any] | None] | None = None,
        automation_summary_updater: Callable[[str, Dict[str, Any]], None] | None = None,
    ):
        self.root = Path(root).resolve()
        self.user_id = user_id or "anonymous"
        self.safe_user_id = _safe_segment(self.user_id, fallback="anonymous")
        self.namespace = _safe_segment(namespace, fallback="automation_executions")
        self.executions_dir = self.root / self.namespace / self.safe_user_id
        self.index_dir = self.root / "indexes" / self.namespace / self.safe_user_id
        self.index_path = self.index_dir / "executions.sqlite"
        self.executions_dir.mkdir(parents=True, exist_ok=True)
        self._automation_loader = automation_loader
        self._automation_summary_updater = automation_summary_updater

    def _execution_signature(self) -> str:
        rows: List[str] = []
        for path in sorted(self.executions_dir.glob("*/*.json")):
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
        for path in (
            self.index_path,
            self.index_path.with_name(self.index_path.name + "-wal"),
            self.index_path.with_name(self.index_path.name + "-shm"),
        ):
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
            CREATE TABLE executions (
                docid INTEGER PRIMARY KEY,
                id TEXT NOT NULL UNIQUE,
                automation_id TEXT NOT NULL,
                automation_title TEXT,
                owner_user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger TEXT,
                source_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                duration_ms INTEGER,
                conversation_id TEXT,
                turn_id TEXT,
                summary TEXT,
                error TEXT,
                log_excerpt TEXT,
                artifact_count INTEGER,
                artifacts_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE executions_fts USING fts5(
                id,
                automation_id,
                automation_title,
                summary,
                error,
                log_excerpt,
                artifacts,
                metadata,
                tokenize = 'unicode61'
            );
            """
        )

        docid = 0
        for path in sorted(self.executions_dir.glob("*/*.json")):
            execution = self._read_json(path)
            if not execution:
                continue
            docid += 1
            artifacts = _json_dict_list(execution.get("artifacts"))
            artifact_terms: List[str] = []
            for artifact in artifacts:
                artifact_terms.extend(
                    str(artifact.get(key) or "")
                    for key in (
                        "id",
                        "filename",
                        "description",
                        "mime_type",
                        "logical_path",
                        "stored_path",
                        "hosted_uri",
                    )
                )
            metadata = execution.get("metadata") if isinstance(execution.get("metadata"), dict) else {}
            result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
            source = execution.get("source") if isinstance(execution.get("source"), dict) else {}
            conn.execute(
                """
                INSERT INTO executions (
                    docid, id, automation_id, automation_title, owner_user_id, status, trigger,
                    source_json, created_at, updated_at, started_at, finished_at, duration_ms,
                    conversation_id, turn_id, summary, error, log_excerpt,
                    artifact_count, artifacts_json, result_json, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    docid,
                    str(execution.get("id") or ""),
                    str(execution.get("automation_id") or ""),
                    str(execution.get("automation_title") or ""),
                    str(execution.get("owner_user_id") or self.user_id),
                    str(execution.get("status") or ""),
                    str(execution.get("trigger") or ""),
                    json.dumps(source),
                    execution.get("created_at"),
                    execution.get("updated_at"),
                    execution.get("started_at"),
                    execution.get("finished_at"),
                    execution.get("duration_ms"),
                    execution.get("conversation_id"),
                    execution.get("turn_id"),
                    str(execution.get("summary") or ""),
                    str(execution.get("error") or ""),
                    str(execution.get("log_excerpt") or ""),
                    len(artifacts),
                    json.dumps(artifacts),
                    json.dumps(result),
                    json.dumps(metadata),
                ),
            )
            conn.execute(
                """
                INSERT INTO executions_fts (
                    rowid, id, automation_id, automation_title, summary, error, log_excerpt, artifacts, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    docid,
                    str(execution.get("id") or ""),
                    str(execution.get("automation_id") or ""),
                    str(execution.get("automation_title") or ""),
                    str(execution.get("summary") or ""),
                    str(execution.get("error") or ""),
                    str(execution.get("log_excerpt") or ""),
                    " ".join(artifact_terms),
                    json.dumps(metadata),
                ),
            )
        conn.execute("INSERT INTO meta (key, value) VALUES ('signature', ?)", (self._execution_signature(),))
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (self.SCHEMA_VERSION,))
        conn.commit()
        conn.close()
        return self.index_path

    def ensure_search_index(self) -> Path:
        signature = self._execution_signature()
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

    def _execution_dir(self, automation_id: str) -> Path:
        safe_automation_id = _safe_segment(automation_id, fallback="automation")
        path = self.executions_dir / safe_automation_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _execution_path(self, *, automation_id: str, execution_id: str) -> Path:
        return self._execution_dir(automation_id) / f"{_safe_segment(execution_id, fallback='execution')}.json"

    def _find_execution_path(self, *, execution_id: str, automation_id: str = "") -> Path | None:
        safe_execution_id = _safe_segment(execution_id, fallback="")
        if not safe_execution_id:
            return None
        if automation_id:
            path = self._execution_path(automation_id=automation_id, execution_id=safe_execution_id)
            return path if path.exists() else None
        matches = list(self.executions_dir.glob(f"*/{safe_execution_id}.json"))
        return matches[0] if matches else None

    @staticmethod
    def _normalize_status(status: str) -> str:
        normalized = (status or "queued").strip().lower()
        if normalized not in {"queued", "running", "success", "failed", "cancelled"}:
            raise ValueError("execution status must be queued, running, success, failed, or cancelled")
        return normalized

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
        return data

    def _write_execution_json(self, *, automation_id: str, execution_id: str, execution: Dict[str, Any]) -> Dict[str, Any]:
        self._write_json(self._execution_path(automation_id=automation_id, execution_id=execution_id), execution)
        try:
            self.rebuild_search_index()
        except Exception:
            pass
        return execution

    def _decode_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "schema_version": "automation_execution.v1",
            "automation_id": row["automation_id"],
            "automation_title": row["automation_title"] or "",
            "owner_user_id": row["owner_user_id"],
            "status": row["status"],
            "trigger": row["trigger"] or "",
            "source": json.loads(row["source_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "duration_ms": row["duration_ms"],
            "conversation_id": row["conversation_id"] or None,
            "turn_id": row["turn_id"] or None,
            "summary": row["summary"] or "",
            "error": row["error"] or "",
            "log_excerpt": row["log_excerpt"] or "",
            "artifact_count": row["artifact_count"],
            "artifacts": json.loads(row["artifacts_json"] or "[]"),
            "result": json.loads(row["result_json"] or "{}"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def _load_automation(self, automation_id: str) -> Dict[str, Any] | None:
        if not self._automation_loader:
            return None
        return self._automation_loader(automation_id)

    def _notify_parent_summary(self, *, automation_id: str, execution: Dict[str, Any]) -> None:
        if not self._automation_summary_updater:
            return
        self._automation_summary_updater(automation_id, execution)

    def create_execution(
        self,
        *,
        automation_id: str,
        status: str = "queued",
        trigger: str = "manual",
        source: Dict[str, Any] | None = None,
        conversation_id: str = "",
        turn_id: str = "",
        summary: str = "",
        result: Dict[str, Any] | None = None,
        error: str = "",
        log_excerpt: str = "",
        metadata: Dict[str, Any] | None = None,
        artifacts: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        automation = self._load_automation(automation_id)
        if self._automation_loader and not automation:
            raise ValueError(f"automation {automation_id!r} was not found")
        automation = automation or {}
        now = _utc_now()
        normalized_status = self._normalize_status(status)
        execution_id = f"exec_{_slug(str(automation.get('title') or automation_id), fallback='automation')}_{uuid.uuid4().hex[:10]}"
        started_at = now if normalized_status == "running" else None
        finished_at = now if normalized_status in {"success", "failed", "cancelled"} else None
        normalized_artifacts = [_normalize_execution_artifact(item) for item in _json_dict_list(artifacts or [])]
        execution = {
            "schema_version": "automation_execution.v1",
            "id": execution_id,
            "automation_id": automation_id,
            "automation_title": str(automation.get("title") or ""),
            "owner_user_id": self.user_id,
            "status": normalized_status,
            "trigger": str(trigger or "manual").strip() or "manual",
            "source": source or {},
            "created_at": now,
            "updated_at": now,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": _duration_ms(started_at, finished_at),
            "conversation_id": str(conversation_id or "").strip() or automation.get("conversation_id") or None,
            "turn_id": str(turn_id or "").strip() or None,
            "summary": str(summary or "").strip(),
            "result": result or {},
            "error": str(error or "").strip(),
            "log_excerpt": str(log_excerpt or "").strip(),
            "artifact_count": len(normalized_artifacts),
            "artifacts": normalized_artifacts,
            "metadata": metadata or {},
        }
        self._write_execution_json(automation_id=automation_id, execution_id=execution_id, execution=execution)
        self._notify_parent_summary(automation_id=automation_id, execution=execution)
        return execution

    def get_execution(self, *, execution_id: str, automation_id: str = "") -> Dict[str, Any] | None:
        path = self._find_execution_path(execution_id=execution_id, automation_id=automation_id)
        if not path:
            return None
        execution = self._read_json(path)
        return execution or None

    def list_executions(
        self,
        *,
        automation_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        status_norm = (status or "").strip().lower()
        paths = (
            sorted(self._execution_dir(automation_id).glob("*.json"))
            if automation_id
            else sorted(self.executions_dir.glob("*/*.json"))
        )
        rows: List[Dict[str, Any]] = []
        for path in paths:
            execution = self._read_json(path)
            if not execution:
                continue
            if status_norm and str(execution.get("status") or "").lower() != status_norm:
                continue
            rows.append(execution)
        rows.sort(
            key=lambda item: str(
                item.get("started_at")
                or item.get("finished_at")
                or item.get("updated_at")
                or item.get("created_at")
                or ""
            ),
            reverse=True,
        )
        return rows[: max(1, int(limit or 50))]

    def search_executions(
        self,
        *,
        query: str = "",
        automation_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            self.ensure_search_index()
            status_norm = (status or "").strip().lower()
            automation_norm = (automation_id or "").strip()
            fts = _fts_query(query)
            where: List[str] = []
            params: List[Any] = []
            if fts:
                where.append("executions_fts MATCH ?")
                params.append(fts)
                from_clause = "FROM executions_fts JOIN executions e ON e.docid = executions_fts.rowid"
                rank_expr = "bm25(executions_fts, 4.0, 4.0, 3.0, 6.0, 4.0, 2.0, 3.0, 1.0)"
                order_by = "rank ASC, e.updated_at DESC, e.created_at DESC"
            else:
                from_clause = "FROM executions e"
                rank_expr = "0.0"
                order_by = "e.updated_at DESC, e.created_at DESC"
            if automation_norm:
                where.append("e.automation_id = ?")
                params.append(automation_norm)
            if status_norm:
                where.append("LOWER(e.status) = ?")
                params.append(status_norm)
            sql = f"""
                SELECT
                    e.id, e.automation_id, e.automation_title, e.owner_user_id, e.status, e.trigger,
                    e.source_json, e.created_at, e.updated_at, e.started_at, e.finished_at, e.duration_ms,
                    e.conversation_id, e.turn_id, e.summary, e.error, e.log_excerpt,
                    e.artifact_count, e.artifacts_json, e.result_json, e.metadata_json,
                    {rank_expr} AS rank
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
                execution = self._decode_row(row)
                execution["score"] = round(-float(row["rank"] or 0.0), 3)
                results.append(execution)
            return results
        except Exception:
            fallback = self.list_executions(automation_id=automation_id, status=status, limit=max(1, int(limit or 50)))
            query_norm = (query or "").strip().lower()
            if not query_norm:
                return fallback
            out: List[Dict[str, Any]] = []
            for execution in fallback:
                artifacts = _json_dict_list(execution.get("artifacts"))
                haystack = "\n".join(
                    [
                        str(execution.get("id") or ""),
                        str(execution.get("automation_id") or ""),
                        str(execution.get("automation_title") or ""),
                        str(execution.get("summary") or ""),
                        str(execution.get("error") or ""),
                        str(execution.get("log_excerpt") or ""),
                        json.dumps(artifacts),
                        json.dumps(execution.get("metadata") or {}),
                    ]
                ).lower()
                if query_norm in haystack:
                    out.append(execution)
            return out[: max(1, int(limit or 50))]

    def update_execution(
        self,
        *,
        execution_id: str,
        automation_id: str = "",
        status: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        summary: str | None = None,
        result: Dict[str, Any] | None = None,
        error: str | None = None,
        log_excerpt: str | None = None,
        artifacts: List[Dict[str, Any]] | None = None,
        append_artifacts: bool = False,
        metadata_patch: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        path = self._find_execution_path(execution_id=execution_id, automation_id=automation_id)
        if not path:
            raise ValueError(f"execution {execution_id!r} was not found")
        execution = self._read_json(path)
        if not execution:
            raise ValueError(f"execution {execution_id!r} could not be read")
        if status is not None:
            normalized = self._normalize_status(status)
            execution["status"] = normalized
            if normalized == "running" and not execution.get("started_at"):
                execution["started_at"] = _utc_now()
            if normalized in {"success", "failed", "cancelled"} and not execution.get("finished_at"):
                execution["finished_at"] = _utc_now()
        if conversation_id is not None:
            execution["conversation_id"] = str(conversation_id or "").strip() or None
        if turn_id is not None:
            execution["turn_id"] = str(turn_id or "").strip() or None
        if summary is not None:
            execution["summary"] = str(summary or "").strip()
        if result is not None:
            execution["result"] = result if isinstance(result, dict) else {"value": result}
        if error is not None:
            execution["error"] = str(error or "").strip()
        if log_excerpt is not None:
            execution["log_excerpt"] = str(log_excerpt or "").strip()
        if artifacts is not None:
            next_artifacts = [_normalize_execution_artifact(item) for item in _json_dict_list(artifacts)]
            if append_artifacts:
                current = _json_dict_list(execution.get("artifacts"))
                by_key: Dict[str, Dict[str, Any]] = {}
                for item in current + next_artifacts:
                    key = str(item.get("id") or item.get("logical_path") or item.get("stored_path") or item.get("filename") or "")
                    if key:
                        by_key[key] = item
                execution["artifacts"] = list(by_key.values())
            else:
                execution["artifacts"] = next_artifacts
        execution["artifact_count"] = len(_json_dict_list(execution.get("artifacts")))
        if metadata_patch:
            metadata = execution.get("metadata") if isinstance(execution.get("metadata"), dict) else {}
            metadata.update(metadata_patch)
            execution["metadata"] = metadata
        execution["updated_at"] = _utc_now()
        execution["duration_ms"] = _duration_ms(execution.get("started_at"), execution.get("finished_at"))
        self._write_execution_json(
            automation_id=str(execution.get("automation_id") or automation_id or "").strip(),
            execution_id=str(execution.get("id") or execution_id),
            execution=execution,
        )
        automation_id_for_summary = str(execution.get("automation_id") or automation_id or "").strip()
        if automation_id_for_summary:
            self._notify_parent_summary(automation_id=automation_id_for_summary, execution=execution)
        return execution

    def delete_execution(self, *, execution_id: str, automation_id: str = "") -> Dict[str, Any] | None:
        path = self._find_execution_path(execution_id=execution_id, automation_id=automation_id)
        if not path:
            return None
        execution = self._read_json(path)
        path.unlink()
        try:
            self.rebuild_search_index()
        except Exception:
            pass
        return execution or None
