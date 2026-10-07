# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""SecretsService COPY-manifest staging with the prepared test interpreter.

This exercises the actual selected source bytes in a separate process. The
interpreter's installed dependencies are explicit test inputs; these checks
do not claim a built image, a fresh package install or deployed AWS custody.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[6]
IMAGE = APP / "deployment/docker/all_in_one_kdcube/Dockerfile_Secrets"
LEAF = "kdcube_ai_app/infra/secrets"
AWS_MODULES = (
    "runtime_aws", "runtime_aws_service", "runtime_pg_access",
    "runtime_pg_metadata", "runtime_pg_schema",
)


def _copies():
    for line in IMAGE.read_text().splitlines():
        if line.startswith("COPY "):
            _, source, destination = shlex.split(line)
            yield APP / source, Path(destination).relative_to("/app")


@pytest.mark.parametrize("module", AWS_MODULES)
def test_secrets_image_explicitly_packages_aws_pg_module(module):
    source = APP / f"src/kdcube-ai-app/{LEAF}/{module}.py"
    assert (source, Path(f"{LEAF}/{module}.py")) in set(_copies())
    assert source.is_file()


def test_secrets_image_uses_shared_pinned_aws_stack_and_service_pg_dependency():
    copies = set(_copies())
    assert (APP / "src/kdcube-ai-app/requirements-aws.txt", Path("aws-requirements.txt")) in copies
    dockerfile = IMAGE.read_text()
    assert "-r /app/aws-requirements.txt" in dockerfile
    requirements = (APP / "deployment/docker/all_in_one_kdcube/secrets/requirements.txt").read_text()
    assert "asyncpg" in requirements.splitlines()
    aws = (APP / "src/kdcube-ai-app/requirements-aws.txt").read_text()
    assert "aioboto3==13.2.0" in aws.splitlines()
    assert "aiobotocore[boto3]==2.15.2" in aws.splitlines()


def test_actual_copy_manifest_imports_service_without_source_tree_fallback_or_resource_io(tmp_path):
    # Stage only actual COPY inputs, not the entire SDK tree. Missing transitive
    # module copies therefore cannot be hidden by the source-test PYTHONPATH.
    for source, relative in _copies():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copyfile(source, destination)
    for relative in ("kdcube_ai_app/infra", LEAF):
        (tmp_path / relative / "__init__.py").touch()
    script = '''
import asyncio
import importlib
from pathlib import Path
import aioboto3
import asyncpg
from fastapi import FastAPI

def forbidden(*args, **kwargs):
    raise AssertionError("resource I/O during image import/construction/install")
aioboto3.Session = forbidden
asyncpg.create_pool = forbidden
from kdcube_ai_app.infra.secrets.runtime_aws_service import (
    RuntimeAwsService, RuntimeAwsServiceConfig, RuntimeBootstrapSecretRef,
)
from kdcube_ai_app.infra.secrets.runtime_contract import RuntimeScopePolicy
root = Path.cwd()
for name in %s:
    module = importlib.import_module("kdcube_ai_app.infra.secrets." + name)
    assert Path(module.__file__).resolve() == root / "kdcube_ai_app/infra/secrets" / (name + ".py")
config = RuntimeAwsServiceConfig(
    database_dsn_ref=RuntimeBootstrapSecretRef(
        "arn:aws:secretsmanager:eu-central-1:123456789012:secret:bootstrap/database-Ab1234", "a" * 32),
    commitment_key_ref=RuntimeBootstrapSecretRef(
        "arn:aws:secretsmanager:eu-central-1:123456789012:secret:bootstrap/commitment-Cd5678", "b" * 32),
    schema="runtime_custody", service_role="runtime_custody_service", namespaces=("custody",),
    cloud_prefix="runtime/custody", account_id="123456789012", region="eu-central-1",
)
service = RuntimeAwsService(config)
app = FastAPI(lifespan=service.lifespan)
service.install_routes(app, policy=RuntimeScopePolicy(None))
assert len([route for route in app.routes if route.path.startswith("/runtime-secrets/")]) == 5
assert service._pool is None and service._commitment_key is None
asyncio.run(service.close())
''' % json.dumps(list(AWS_MODULES))
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
