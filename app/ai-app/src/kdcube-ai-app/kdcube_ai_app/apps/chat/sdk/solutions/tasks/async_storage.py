from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from .storage import TaskStorage
    from .executions_storage import TaskExecutionsStorage
except ImportError:
    import importlib.util
    import sys

    def _load_sibling(module_name: str, filename: str):
        module_path = Path(__file__).with_name(filename)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load sibling module {filename}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(module_name, module)
        spec.loader.exec_module(module)
        return module

    _storage_mod = _load_sibling("_kdcube_tasks_storage", "storage.py")
    _executions_mod = _load_sibling("_kdcube_tasks_executions_storage", "executions_storage.py")
    TaskStorage = _storage_mod.TaskStorage
    TaskExecutionsStorage = _executions_mod.TaskExecutionsStorage


class AsyncTaskExecutionsStorage:
    """Async boundary for execution/subagent event storage."""

    def __init__(self, inner: TaskExecutionsStorage):
        self._inner = inner

    async def list_executions(
        self,
        *,
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._inner.list_executions, task_id=task_id, status=status, limit=limit)

    async def search_executions(
        self,
        *,
        query: str = "",
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._inner.search_executions,
            query=query,
            task_id=task_id,
            status=status,
            limit=limit,
        )

    async def get_execution(self, *, execution_id: str, task_id: str = "") -> Dict[str, Any] | None:
        return await asyncio.to_thread(self._inner.get_execution, execution_id=execution_id, task_id=task_id)

    async def create_execution(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self._inner.create_execution, **kwargs)

    async def update_execution(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self._inner.update_execution, **kwargs)

    async def delete_execution(self, *, execution_id: str, task_id: str = "") -> Dict[str, Any] | None:
        return await asyncio.to_thread(self._inner.delete_execution, execution_id=execution_id, task_id=task_id)

    async def ensure_search_index(self) -> Path:
        return await asyncio.to_thread(self._inner.ensure_search_index)

    async def rebuild_search_index(self) -> Path:
        return await asyncio.to_thread(self._inner.rebuild_search_index)


class AsyncTaskStorage:
    """Async boundary for the bundle's local task storage.

    The current storage backend is file + SQLite based and intentionally small.
    Async callers must use this boundary so filesystem and SQLite work does not
    run on the event loop.
    """

    def __init__(self, root: str | Path, *, user_id: str):
        self._inner = TaskStorage(root, user_id=user_id)
        self.executions = AsyncTaskExecutionsStorage(self._inner.executions)

    @property
    def user_id(self) -> str:
        return self._inner.user_id

    @property
    def safe_user_id(self) -> str:
        return self._inner.safe_user_id

    @property
    def root(self) -> Path:
        return self._inner.root

    async def list_tasks(self, *, status: str = "", query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._inner.list_tasks, status=status, query=query, limit=limit)

    async def search_tasks(self, *, query: str = "", status: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._inner.search_tasks, query=query, status=status, limit=limit)

    async def get_task(self, task_id: str) -> Dict[str, Any] | None:
        return await asyncio.to_thread(self._inner.get_task, task_id)

    async def create_task(
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
        return await asyncio.to_thread(
            self._inner.create_task,
            title=title,
            description=description,
            schedule_cron=schedule_cron,
            timezone_name=timezone_name,
            recurring=recurring,
            labels=labels,
            source=source,
            conversation_id=conversation_id,
        )

    async def update_task(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self._inner.update_task, **kwargs)

    async def delete_task(self, *, task_id: str, hard: bool = False) -> Dict[str, Any] | None:
        return await asyncio.to_thread(self._inner.delete_task, task_id=task_id, hard=hard)

    async def set_task_status(self, *, task_id: str, status: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self._inner.set_task_status, task_id=task_id, status=status)

    async def link_task(
        self,
        *,
        task_id: str,
        target_task_id: str,
        relation: str = "related",
        reciprocal: bool = True,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._inner.link_task,
            task_id=task_id,
            target_task_id=target_task_id,
            relation=relation,
            reciprocal=reciprocal,
        )

    async def list_executions(
        self,
        *,
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await self.executions.list_executions(task_id=task_id, status=status, limit=limit)

    async def search_executions(
        self,
        *,
        query: str = "",
        task_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return await self.executions.search_executions(
            query=query,
            task_id=task_id,
            status=status,
            limit=limit,
        )

    async def get_execution(self, *, execution_id: str, task_id: str = "") -> Dict[str, Any] | None:
        return await self.executions.get_execution(execution_id=execution_id, task_id=task_id)

    async def create_execution(self, **kwargs) -> Dict[str, Any]:
        return await self.executions.create_execution(**kwargs)

    async def update_execution(self, **kwargs) -> Dict[str, Any]:
        return await self.executions.update_execution(**kwargs)

    async def delete_execution(self, *, execution_id: str, task_id: str = "") -> Dict[str, Any] | None:
        return await self.executions.delete_execution(execution_id=execution_id, task_id=task_id)

    async def attach_execution_history(
        self,
        tasks: Iterable[Dict[str, Any]],
        *,
        execution_limit: int = 3,
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(
            self._inner.attach_execution_history,
            tasks,
            execution_limit=execution_limit,
        )

    async def ensure_search_index(self) -> Path:
        return await asyncio.to_thread(self._inner.ensure_search_index)

    async def rebuild_search_index(self) -> Path:
        return await asyncio.to_thread(self._inner.rebuild_search_index)

    async def ensure_execution_search_index(self) -> Path:
        return await self.executions.ensure_search_index()


async def list_task_user_ids(root: str | Path) -> list[str]:
    def _scan() -> list[str]:
        tasks_root = Path(root).resolve() / "tasks"
        if not tasks_root.exists():
            return []
        return [path.name for path in sorted(tasks_root.iterdir()) if path.is_dir()]

    return await asyncio.to_thread(_scan)
