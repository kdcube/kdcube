from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(raw: str, *, fallback: str = "default") -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw or "").strip("-")
    return value or fallback


class TelegramUserAdminStorage:
    """File-backed Telegram user registry shared by Telegram integrations."""

    SCHEMA_VERSION = "telegram-user-admin.v1"
    ALLOWED_ROLES = ("anonymous", "registered", "admin")

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.admin_dir = self.root / "admin"
        self.path = self.admin_dir / "telegram-users.json"
        self.updates_dir = self.admin_dir / "telegram-updates"
        self.updates_state_path = self.updates_dir / "state.json"
        self.admin_dir.mkdir(parents=True, exist_ok=True)
        self.updates_dir.mkdir(parents=True, exist_ok=True)

    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": _utc_now(),
            "users": [],
        }

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        users = data.get("users")
        if not isinstance(users, list):
            data["users"] = []
        data["schema_version"] = str(data.get("schema_version") or self.SCHEMA_VERSION)
        return data

    def _write(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data["schema_version"] = self.SCHEMA_VERSION
        data["updated_at"] = _utc_now()
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.path)
        return data

    @classmethod
    def _normalize_role(cls, value: str) -> str:
        role = (value or "anonymous").strip().lower()
        return role if role in cls.ALLOWED_ROLES else "anonymous"

    @staticmethod
    def _default_conversation_id(
        *,
        telegram_user_id: str,
        telegram_chat_id: str = "",
        kdcube_user_id: str = "",
    ) -> str:
        if kdcube_user_id:
            return f"kdcube_user_{_safe_segment(kdcube_user_id)}"
        if telegram_chat_id:
            return f"telegram_chat_{_safe_segment(telegram_chat_id)}"
        return f"telegram_user_{_safe_segment(telegram_user_id)}"

    @staticmethod
    def _conversation_row(
        conversation_id: str,
        *,
        title: str = "",
        created_at: str = "",
        updated_at: str = "",
        source: str = "telegram",
    ) -> Dict[str, Any]:
        conv_id = str(conversation_id or "").strip()
        now = _utc_now()
        return {
            "conversation_id": conv_id,
            "title": str(title or "").strip() or "Telegram chat",
            "source": str(source or "telegram").strip() or "telegram",
            "created_at": str(created_at or now).strip(),
            "updated_at": str(updated_at or now).strip(),
        }

    @classmethod
    def _normalize_conversations(cls, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in user.get("conversations") or []:
            if not isinstance(raw, dict):
                continue
            conv_id = str(raw.get("conversation_id") or "").strip()
            if not conv_id or conv_id in seen:
                continue
            seen.add(conv_id)
            rows.append(
                cls._conversation_row(
                    conv_id,
                    title=str(raw.get("title") or "").strip(),
                    created_at=str(raw.get("created_at") or "").strip(),
                    updated_at=str(raw.get("updated_at") or "").strip(),
                    source=str(raw.get("source") or "telegram").strip(),
                )
            )
        active = str(user.get("conversation_id") or "").strip()
        if active and active not in seen:
            rows.insert(
                0,
                cls._conversation_row(
                    active,
                    title="Current chat",
                    source="telegram_active_mapping",
                    created_at=str(user.get("created_at") or "").strip(),
                    updated_at=str(user.get("updated_at") or "").strip(),
                ),
            )
        rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return rows

    @classmethod
    def _with_normalized_conversations(cls, user: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(user)
        item["conversations"] = cls._normalize_conversations(item)
        return item

    def list_users(self) -> List[Dict[str, Any]]:
        rows = []
        for raw in self._read().get("users") or []:
            if isinstance(raw, dict) and str(raw.get("telegram_user_id") or "").strip():
                rows.append(self._with_normalized_conversations(raw))
        return sorted(rows, key=lambda item: (str(item.get("role") or ""), str(item.get("telegram_user_id") or "")))

    def upsert_user(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str = "",
        telegram_username: str = "",
        kdcube_user_id: str = "",
        role: str = "anonymous",
        conversation_id: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        telegram_id = str(telegram_user_id or "").strip()
        if not telegram_id:
            raise ValueError("telegram_user_id is required")
        chat_id = str(telegram_chat_id or "").strip()
        kdcube_id = str(kdcube_user_id or "").strip()
        normalized_role = self._normalize_role(role)
        conv_id = str(conversation_id or "").strip() or self._default_conversation_id(
            telegram_user_id=telegram_id,
            telegram_chat_id=chat_id,
            kdcube_user_id=kdcube_id,
        )
        now = _utc_now()
        data = self._read()
        users = [item for item in data.get("users") or [] if isinstance(item, dict)]
        existing = next((item for item in users if str(item.get("telegram_user_id") or "") == telegram_id), None)
        if not existing:
            existing = {
                "telegram_user_id": telegram_id,
                "created_at": now,
            }
            users.append(existing)
        conversations = self._normalize_conversations(existing)
        if conv_id and all(str(item.get("conversation_id") or "") != conv_id for item in conversations):
            conversations.insert(
                0,
                self._conversation_row(
                    conv_id,
                    title="Current chat",
                    source="telegram_admin_mapping",
                    created_at=str(existing.get("created_at") or now),
                    updated_at=now,
                ),
            )
        existing.update(
            {
                "telegram_user_id": telegram_id,
                "telegram_chat_id": chat_id,
                "telegram_username": str(telegram_username or "").strip(),
                "kdcube_user_id": kdcube_id,
                "role": normalized_role,
                "conversation_id": conv_id,
                "conversations": conversations,
                "notes": str(notes or "").strip(),
                "updated_at": now,
            }
        )
        data["users"] = users
        self._write(data)
        return existing

    def delete_user(self, *, telegram_user_id: str) -> bool:
        telegram_id = str(telegram_user_id or "").strip()
        data = self._read()
        users = [item for item in data.get("users") or [] if isinstance(item, dict)]
        kept = [item for item in users if str(item.get("telegram_user_id") or "") != telegram_id]
        if len(kept) == len(users):
            return False
        data["users"] = kept
        self._write(data)
        return True

    def resolve_telegram_user(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str = "",
        telegram_username: str = "",
        create_if_missing: bool = True,
    ) -> Dict[str, Any]:
        telegram_id = str(telegram_user_id or "").strip()
        if not telegram_id:
            telegram_id = str(telegram_chat_id or "anonymous").strip() or "anonymous"
        for item in self.list_users():
            if str(item.get("telegram_user_id") or "") == telegram_id:
                return item
        if not create_if_missing:
            return {
                "telegram_user_id": telegram_id,
                "telegram_chat_id": str(telegram_chat_id or "").strip(),
                "telegram_username": str(telegram_username or "").strip(),
                "kdcube_user_id": "",
                "role": "anonymous",
                "conversation_id": self._default_conversation_id(
                    telegram_user_id=telegram_id,
                    telegram_chat_id=str(telegram_chat_id or "").strip(),
                ),
            }
        return self.upsert_user(
            telegram_user_id=telegram_id,
            telegram_chat_id=str(telegram_chat_id or "").strip(),
            telegram_username=str(telegram_username or "").strip(),
            role="anonymous",
        )

    def list_conversations(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str = "",
        telegram_username: str = "",
        create_if_missing: bool = False,
    ) -> Dict[str, Any]:
        user = self.resolve_telegram_user(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            telegram_username=telegram_username,
            create_if_missing=create_if_missing,
        )
        user = self._with_normalized_conversations(user)
        return {
            "user": user,
            "active_conversation_id": str(user.get("conversation_id") or "").strip(),
            "conversations": user.get("conversations") or [],
        }

    def create_conversation(
        self,
        *,
        telegram_user_id: str,
        telegram_chat_id: str = "",
        telegram_username: str = "",
        title: str = "",
    ) -> Dict[str, Any]:
        user = self.resolve_telegram_user(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            telegram_username=telegram_username,
            create_if_missing=True,
        )
        conv_id = (
            f"telegram_chat_{_safe_segment(str(user.get('telegram_chat_id') or telegram_chat_id or telegram_user_id))}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        now = _utc_now()
        conversations = self._normalize_conversations(user)
        conversations.insert(
            0,
            self._conversation_row(
                conv_id,
                title=title or "New Telegram chat",
                source="telegram_widget",
                created_at=now,
                updated_at=now,
            ),
        )
        updated = self.upsert_user(
            telegram_user_id=str(user.get("telegram_user_id") or telegram_user_id),
            telegram_chat_id=str(user.get("telegram_chat_id") or telegram_chat_id),
            telegram_username=str(user.get("telegram_username") or telegram_username),
            kdcube_user_id=str(user.get("kdcube_user_id") or ""),
            role=str(user.get("role") or "anonymous"),
            conversation_id=conv_id,
            notes=str(user.get("notes") or ""),
        )
        updated["conversations"] = conversations
        data = self._read()
        users = [item for item in data.get("users") or [] if isinstance(item, dict)]
        for item in users:
            if str(item.get("telegram_user_id") or "") == str(updated.get("telegram_user_id") or ""):
                item["conversations"] = conversations
                break
        data["users"] = users
        self._write(data)
        return self.list_conversations(
            telegram_user_id=str(updated.get("telegram_user_id") or telegram_user_id),
            telegram_chat_id=str(updated.get("telegram_chat_id") or telegram_chat_id),
            telegram_username=str(updated.get("telegram_username") or telegram_username),
        )

    def switch_conversation(
        self,
        *,
        telegram_user_id: str,
        conversation_id: str,
    ) -> Dict[str, Any]:
        telegram_id = str(telegram_user_id or "").strip()
        conv_id = str(conversation_id or "").strip()
        if not telegram_id or not conv_id:
            return {"ok": False, "error": {"code": "conversation_required", "message": "telegram_user_id and conversation_id are required."}}
        data = self._read()
        users = [item for item in data.get("users") or [] if isinstance(item, dict)]
        user = next((item for item in users if str(item.get("telegram_user_id") or "") == telegram_id), None)
        if not user:
            return {"ok": False, "error": {"code": "telegram_user_not_found", "message": "Telegram user mapping was not found."}}
        conversations = self._normalize_conversations(user)
        row = next((item for item in conversations if str(item.get("conversation_id") or "") == conv_id), None)
        if not row:
            return {"ok": False, "error": {"code": "conversation_not_found", "message": "Conversation is not registered for this Telegram user."}}
        now = _utc_now()
        row["updated_at"] = now
        user["conversation_id"] = conv_id
        user["conversations"] = conversations
        user["updated_at"] = now
        data["users"] = users
        self._write(data)
        return self.list_conversations(telegram_user_id=telegram_id)

    def delete_conversation(
        self,
        *,
        telegram_user_id: str,
        conversation_id: str,
    ) -> Dict[str, Any]:
        telegram_id = str(telegram_user_id or "").strip()
        conv_id = str(conversation_id or "").strip()
        data = self._read()
        users = [item for item in data.get("users") or [] if isinstance(item, dict)]
        user = next((item for item in users if str(item.get("telegram_user_id") or "") == telegram_id), None)
        if not user or not conv_id:
            return {"ok": False, "deleted": False, "error": {"code": "conversation_not_found", "message": "Conversation was not found."}}
        conversations = self._normalize_conversations(user)
        kept = [item for item in conversations if str(item.get("conversation_id") or "") != conv_id]
        deleted = len(kept) != len(conversations)
        now = _utc_now()
        if not kept:
            fallback_base = str(user.get("telegram_chat_id") or telegram_id)
            fallback = self._conversation_row(
                f"telegram_chat_{_safe_segment(fallback_base)}_{uuid.uuid4().hex[:8]}",
                title="Telegram chat",
                source="telegram_default",
                created_at=now,
                updated_at=now,
            )
            kept = [fallback]
        active = str(user.get("conversation_id") or "").strip()
        if active == conv_id or not any(str(item.get("conversation_id") or "") == active for item in kept):
            user["conversation_id"] = str(kept[0].get("conversation_id") or "")
        user["conversations"] = kept
        user["updated_at"] = now
        data["users"] = users
        self._write(data)
        listing = self.list_conversations(telegram_user_id=telegram_id)
        listing["deleted"] = deleted
        listing["deleted_conversation_id"] = conv_id if deleted else ""
        return listing

    def _update_id_path(self, update_id: str) -> Path:
        return self.updates_dir / f"{_safe_segment(update_id, fallback='update')}.json"

    def _update_lock_dir(self, update_id: str) -> Path:
        return self.updates_dir / f"{_safe_segment(update_id, fallback='update')}.lock"

    def _read_update_record(self, update_id: str) -> Dict[str, Any]:
        path = self._update_id_path(update_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_update_record(self, update_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        path = self._update_id_path(update_id)
        payload = dict(data or {})
        payload["update_id"] = str(update_id or "").strip()
        payload["updated_at"] = _utc_now()
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
        return payload

    def _read_updates_state(self) -> Dict[str, Any]:
        if not self.updates_state_path.exists():
            return {"max_seen_update_id": None}
        try:
            data = json.loads(self.updates_state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"max_seen_update_id": None}
        return data if isinstance(data, dict) else {"max_seen_update_id": None}

    def _write_updates_state(self, data: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(data or {})
        payload["updated_at"] = _utc_now()
        tmp_path = self.updates_state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.updates_state_path)
        return payload

    def _mark_update_seen(self, update_id: str) -> None:
        try:
            numeric = int(str(update_id or "").strip())
        except Exception:
            return
        state = self._read_updates_state()
        try:
            current = int(state.get("max_seen_update_id"))
        except Exception:
            current = None
        if current is None or numeric > current:
            state["max_seen_update_id"] = numeric
            self._write_updates_state(state)

    def claim_telegram_update(self, *, update_id: str, stale_after_seconds: int = 900) -> Dict[str, Any]:
        update = str(update_id or "").strip()
        if not update:
            return {"claimed": True, "status": "untracked", "update_id": ""}

        existing = self._read_update_record(update)
        status = str(existing.get("status") or "").strip().lower()
        if status in {"completed", "failed"}:
            return {"claimed": False, "status": status, "update_id": update, "record": existing}
        if not existing:
            try:
                numeric = int(update)
                max_seen = int(self._read_updates_state().get("max_seen_update_id"))
            except Exception:
                numeric = None
                max_seen = None
            if numeric is not None and max_seen is not None and numeric <= max_seen:
                return {"claimed": False, "status": "stale-update", "update_id": update, "record": {}}

        lock_dir = self._update_lock_dir(update)
        now = time.time()
        if lock_dir.exists():
            try:
                age = now - lock_dir.stat().st_mtime
            except Exception:
                age = 0
            if age < stale_after_seconds:
                return {"claimed": False, "status": "processing", "update_id": update, "record": existing}
            try:
                shutil.rmtree(lock_dir)
            except Exception:
                return {"claimed": False, "status": "processing", "update_id": update, "record": existing}

        try:
            lock_dir.mkdir()
        except FileExistsError:
            return {"claimed": False, "status": "processing", "update_id": update, "record": existing}

        record = self._write_update_record(
            update,
            {
                "status": "processing",
                "started_at": existing.get("started_at") or _utc_now(),
                "claimed_at": _utc_now(),
            },
        )
        self._mark_update_seen(update)
        return {"claimed": True, "status": "processing", "update_id": update, "record": record}

    def complete_telegram_update(self, *, update_id: str, result: Dict[str, Any] | None = None) -> Dict[str, Any]:
        update = str(update_id or "").strip()
        if not update:
            return {"status": "untracked", "update_id": ""}
        record = self._read_update_record(update)
        record.update(
            {
                "status": "completed",
                "completed_at": _utc_now(),
                "stage": (result or {}).get("stage") if isinstance(result, dict) else None,
            }
        )
        if isinstance(result, dict):
            for key in ("summary", "telegram_response", "telegram_delivery"):
                if result.get(key) is not None:
                    record[key] = result[key]
        out = self._write_update_record(update, {k: v for k, v in record.items() if v is not None})
        try:
            shutil.rmtree(self._update_lock_dir(update))
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return out

    def fail_telegram_update(self, *, update_id: str, error: str) -> Dict[str, Any]:
        update = str(update_id or "").strip()
        if not update:
            return {"status": "untracked", "update_id": ""}
        record = self._read_update_record(update)
        record.update(
            {
                "status": "failed",
                "failed_at": _utc_now(),
                "error": str(error or "").strip()[:2000],
            }
        )
        out = self._write_update_record(update, record)
        try:
            shutil.rmtree(self._update_lock_dir(update))
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return out


__all__ = ["TelegramUserAdminStorage"]
