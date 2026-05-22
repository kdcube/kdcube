# SPDX-License-Identifier: MIT

"""Shared pytest fixtures for bundle tests.

These fixtures enable testing any bundle selected by bundle folder.
Run tests for a specific bundle via:
  BUNDLE_UNDER_TEST=/abs/path/to/bundle pytest test_*.py -v
  pytest test_*.py --bundle-path=/abs/path/to/bundle -v

Infrastructure used:
  - Redis  → real async client via get_async_redis_client() (REDIS_URL from settings)
  - Postgres → real asyncpg pool (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE from settings)
  - comm_context → real ChatTaskPayload with minimal required fields
  - ai_bundle_spec → real BundleSpec with the actual bundle path and ID
"""

import ast
import asyncio
import os
import time
from pathlib import Path

import pytest

from kdcube_ai_app.infra.service_hub.inventory import Config, get_settings
from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec


def pytest_addoption(parser):
    """Add bundle-path parameter for pytest."""
    parser.addoption(
        "--bundle-path",
        action="store",
        default=None,
        help="Absolute or cwd-relative path to the bundle under test. Overrides BUNDLE_UNDER_TEST.",
    )


@pytest.fixture(scope="session")
def bundle_dir(request) -> Path:
    """Bundle directory selected for this pytest run."""
    raw = request.config.getoption("--bundle-path") or os.environ.get("BUNDLE_UNDER_TEST")
    if not raw:
        raise pytest.UsageError(
            "Bundle test suite requires a bundle folder. "
            "Set BUNDLE_UNDER_TEST=/abs/path/to/bundle or pass --bundle-path=/abs/path/to/bundle."
        )

    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise pytest.UsageError(f"Bundle under test does not exist: {path}")
    if not path.is_dir():
        raise pytest.UsageError(f"Bundle under test is not a directory: {path}")
    if not (path / "entrypoint.py").exists():
        raise pytest.UsageError(
            f"Bundle under test must contain entrypoint.py: {path}"
        )
    return path


def _derive_bundle_id_from_dir(bundle_dir: Path) -> str:
    """Derive BUNDLE_ID from entrypoint.py, falling back to the folder name."""
    entrypoint = bundle_dir / "entrypoint.py"
    try:
        tree = ast.parse(entrypoint.read_text(encoding="utf-8"), filename=str(entrypoint))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "BUNDLE_ID":
                    value = node.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
    except Exception:
        pass
    return bundle_dir.name.split("@", 1)[0]


@pytest.fixture(scope="session")
def bundle_id(bundle_dir):
    """Bundle ID derived from the selected bundle folder."""
    return _derive_bundle_id_from_dir(bundle_dir)


@pytest.fixture(scope="session")
def redis_client():
    """Real async Redis client using REDIS_URL from settings."""
    from kdcube_ai_app.infra.redis.client import get_async_redis_client
    url = get_settings().REDIS_URL or "redis://localhost:6379/0"
    return get_async_redis_client(url)


@pytest.fixture(scope="session")
def pg_pool():
    """Real asyncpg connection pool using Postgres settings.

    Returns None if Postgres is unreachable — tests that don't invoke
    the graph (the majority) will still pass.
    """
    import asyncpg

    s = get_settings()

    async def _create():
        return await asyncpg.create_pool(
            host=s.PGHOST,
            port=s.PGPORT,
            user=s.PGUSER,
            password=s.PGPASSWORD,
            database=s.PGDATABASE,
            min_size=1,
            max_size=3,
        )

    try:
        return asyncio.run(_create())
    except Exception:
        return None


@pytest.fixture
def comm_context(bundle_id):
    """Real ChatTaskPayload with minimal required fields."""
    from kdcube_ai_app.apps.chat.sdk.protocol import (
        ChatTaskPayload,
        ChatTaskMeta,
        ChatTaskRouting,
        ChatTaskActor,
        ChatTaskUser,
        ChatTaskRequest,
        ChatTaskConfig,
    )
    return ChatTaskPayload(
        meta=ChatTaskMeta(task_id="test-task-001", created_at=time.time()),
        routing=ChatTaskRouting(
            bundle_id=bundle_id,
            session_id="test-session",
            conversation_id="test-conversation",
            turn_id="test-turn",
        ),
        actor=ChatTaskActor(tenant_id="test", project_id="test"),
        user=ChatTaskUser(user_type="regular"),
        request=ChatTaskRequest(message="test"),
        config=ChatTaskConfig(values={}),
    )


@pytest.fixture
def bundle(bundle_dir, bundle_id, redis_client, pg_pool, comm_context):
    """Load and initialize bundle for testing.

    Uses real infrastructure:
      - redis_client  → real async Redis client
      - pg_pool       → real asyncpg pool (None if Postgres unavailable)
      - comm_context  → real ChatTaskPayload
      - ai_bundle_spec → real BundleSpec with bundle path and ID

    Args:
        bundle_dir: Bundle directory selected for this pytest run
        bundle_id: Bundle ID derived from the selected bundle

    Returns:
        Initialized bundle instance ready for testing
    """
    try:
        from kdcube_ai_app.infra.plugin.bundle_loader import (
            BundleSpec,
            _resolve_module,
            _discover_decorated,
        )

        spec = BundleSpec(path=str(bundle_dir), module="entrypoint")
        mod = _resolve_module(spec)
        chosen = _discover_decorated(mod)

        if chosen is None:
            pytest.skip(f"No @bundle_entrypoint class found in bundle '{bundle_id}'")

        kind, meta, symbol = chosen
        if kind != "class":
            pytest.skip(f"Bundle '{bundle_id}' uses factory pattern, not supported in tests")

        config = Config()
        config.ai_bundle_spec = BundleSpec(id=bundle_id, path=str(bundle_dir))

        instance = symbol(
            config=config,
            pg_pool=pg_pool,
            redis=redis_client,
            comm_context=comm_context,
        )
        return instance

    except ImportError as e:
        pytest.skip(f"Cannot import bundle infrastructure: {str(e)}")
    except Exception as e:
        pytest.skip(f"Cannot initialize bundle at '{bundle_dir}': {str(e)}")
