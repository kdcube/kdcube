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


# Qualification is the host's explicit namespace enrollment plus one signed, read-only AWS probe.
# It does not claim expiry-aware reads, replacement-safe purge or IAM isolation.

class _ListClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def list_secrets(self, **request):
        self.calls.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _AwsCodeError(Exception):
    def __init__(self, code):
        super().__init__("synthetic-aws-error-text-must-not-leak")
        self.response = {"Error": {"Code": code, "Message": "synthetic-aws-error-text-must-not-leak"}}


def _enrolled(client, namespaces=(NAMESPACE,)):
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="proc", aws_sm_prefix="synthetic/qualify",
        runtime_secret_namespaces=namespaces,
    ))

    class _Context:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *_exc):
            return False

    manager._client_cm = lambda: _Context()
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{"SecretList": []}, {"SecretList": [{"Name": "x"}], "NextToken": "n"}])
async def test_an_enrolled_namespace_with_a_well_formed_aws_answer_qualifies(response):
    client = _ListClient(response)
    assert await _enrolled(client).qualify_runtime_custody(namespace=NAMESPACE) is True
    assert client.calls == [{"Filters": [{"Key": "name", "Values": [f"synthetic/qualify/runtime/{NAMESPACE}/"]}],
                             "MaxResults": 1}]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["AccessDenied", "AccessDeniedException", "UnauthorizedException"])
async def test_an_aws_access_denial_does_not_qualify(code):
    assert await _enrolled(_ListClient(_AwsCodeError(code))).qualify_runtime_custody(namespace=NAMESPACE) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    _AwsCodeError("ThrottlingException"), _AwsCodeError("InternalServiceError"),
    ConnectionError("synthetic-aws-error-text-must-not-leak"), TimeoutError(),
])
async def test_any_other_aws_failure_is_unavailable_not_a_verdict(failure):
    with pytest.raises(SecretsManagerError, match="^Runtime secret qualification is unavailable$") as raised:
        await _enrolled(_ListClient(failure)).qualify_runtime_custody(namespace=NAMESPACE)
    assert "synthetic-aws-error-text-must-not-leak" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {}, {"SecretList": None}, {"SecretList": {}}, [], "SecretList"])
async def test_a_malformed_aws_answer_is_unavailable(response):
    with pytest.raises(SecretsManagerError, match="^Runtime secret qualification is unavailable$"):
        await _enrolled(_ListClient(response)).qualify_runtime_custody(namespace=NAMESPACE)


@pytest.mark.asyncio
@pytest.mark.parametrize("namespaces, namespace", [
    ((NAMESPACE,), "different-scope"), ((), NAMESPACE), ([], NAMESPACE),
    ((NAMESPACE,), NAMESPACE.upper()), ((NAMESPACE,), ""), ((NAMESPACE,), "a" * 65), ((NAMESPACE,), "x/y"),
])
async def test_an_unenrolled_or_invalid_namespace_does_not_qualify_and_sends_nothing(namespaces, namespace):
    client = _ListClient({"SecretList": []})
    assert await _enrolled(client, namespaces).qualify_runtime_custody(namespace=namespace) is False
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["set", "create", "get", "delete", "purge"])
async def test_one_qualified_namespace_does_not_open_raw_operations_for_another(operation):
    client = _ListClient({"SecretList": []})
    manager = _enrolled(client)
    assert await manager.qualify_runtime_custody(namespace=NAMESPACE) is True
    arguments = {"namespace": "different-scope", "secret_ref": REF}
    if operation in {"set", "create"}:
        arguments.update(value="synthetic-scope-canary", expires_at=2_000_000_000)
    if operation == "purge":
        arguments = {"namespace": "different-scope", "now": 10, "limit": 1}
        method = manager.purge_expired_ephemeral_secrets
    else:
        method = getattr(manager, operation + "_ephemeral_secret")
    with pytest.raises(SecretsManagerError, match="^runtime_secret_scope_forbidden$"):
        await method(**arguments)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_the_hub_store_qualifies_an_enrolled_aws_namespace():
    from kdcube_ai_app.infra.secrets.ephemeral import ephemeral_secret_store

    store = ephemeral_secret_store(namespace=NAMESPACE, manager=_enrolled(_ListClient({"SecretList": []})))
    assert await store.qualify_durable_backend() is True
