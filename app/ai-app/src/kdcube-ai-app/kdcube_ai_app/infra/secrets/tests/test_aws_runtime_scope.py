"""AWS runtime-scope policy tests; no AWS endpoint or IAM claim."""
from __future__ import annotations

import pytest

from kdcube_ai_app.infra.secrets.manager import (
    AwsSecretsManagerSecretsManager, SecretsManagerConfig, SecretsManagerError,
)

NAMESPACE = "aws-scope-test"
REF = "b" * 32


@pytest.mark.asyncio
@pytest.mark.parametrize("namespaces", [(), [], ("different-scope",), ["different-scope"]])
@pytest.mark.parametrize("operation", ["set", "create", "get", "delete", "purge"])
async def test_unenrolled_raw_aws_runtime_operation_refuses_before_client_io(namespaces, operation):
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="proc", runtime_secret_namespaces=namespaces,
    ))
    reached_client = False

    def forbidden_client():
        nonlocal reached_client
        reached_client = True
        raise AssertionError("ungranted namespace reached AWS client")

    manager._client_cm = forbidden_client
    arguments = {"namespace": NAMESPACE, "secret_ref": REF}
    if operation in {"set", "create"}:
        arguments.update(value="synthetic-scope-canary", expires_at=2_000_000_000)
    method = getattr(manager, operation + "_ephemeral_secret") if operation != "purge" else (
        manager.purge_expired_ephemeral_secrets
    )
    if operation == "purge":
        arguments = {"namespace": NAMESPACE, "now": 10, "limit": 1}
    with pytest.raises(SecretsManagerError, match="^runtime_secret_scope_forbidden$"):
        await method(**arguments)
    assert reached_client is False


def test_explicit_namespace_enrollment_preserves_exact_runtime_address():
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="proc", aws_sm_prefix="synthetic/runtime-scope",
        runtime_secret_namespaces=(NAMESPACE,),
    ))
    assert manager._ephemeral_secret_id(NAMESPACE, REF) == (
        f"synthetic/runtime-scope/runtime/{NAMESPACE}/{REF}"
    )
