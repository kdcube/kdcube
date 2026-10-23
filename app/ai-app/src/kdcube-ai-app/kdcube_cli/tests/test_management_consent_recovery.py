"""A management denial's consent recovery: a request permit or a Card grant opens the same consent page (W929)."""
from __future__ import annotations

import time

import pytest

from kdcube_cli.management.models import ConsentRecovery, ManagementRequest, ManagementTarget

BASE = "https://runtime.example"


def _request():
    target = ManagementTarget.create(public_base_url=BASE, tenant="tenant-a", project="project-a")
    return ManagementRequest.secret_write(target, scope="bundle", key="connections.signing_key",
                                          value="unit-value", bundle_id="connection-hub@1-0",
                                          invocation_id="invoke-1")


def _recovery(request, **changes):
    value = {
        "type": "consent_required",
        "reason": "delegated_grant_required",
        "authorization_url": (f"{BASE}/api/integrations/bundles/tenant-a/project-a/connection-hub%401-0/"
                              "widgets/connections_settings?tab=delegated_by_kdcube&pending_agent_grant=1"),
        "access_id": "access-1",
        "resource": request.resource,
        "operation": request.operation,
        "invocation_id": request.invocation_id,
        "request_digest": request.request_digest or "a" * 64,  # keyed operations: the server's keyed digest
        "card_revision": 7,
        "expires_at": int(time.time()) + 600,
        "choices": ["allow_once", "allow_always"],
    }
    value.update(changes)
    return value


@pytest.mark.parametrize("reason", ["delegated_grant_required", "delegated_request_permit_required"])
def test_a_card_grant_and_a_request_permit_are_both_recoveries(reason):
    request = _request()
    extra = {"catalog_version": "catalog-3"} if reason == "delegated_request_permit_required" else {}
    recovery = ConsentRecovery.from_mapping(_recovery(request, reason=reason, **extra), request=request)
    assert recovery.reason == reason
    assert recovery.resource == request.resource and recovery.invocation_id == "invoke-1"


@pytest.mark.parametrize("changes", [
    {"reason": "something_else"},
    {"authorization_url": "https://elsewhere.example/api/integrations/bundles/tenant-a/project-a/"
                          "connection-hub%401-0/widgets/connections_settings"},
    {"authorization_url": f"{BASE}/some/other/page"},
    {"invocation_id": "another-invocation"},
    {"choices": ["allow_forever"]},
])
def test_a_card_grant_recovery_keeps_every_existing_check(changes):
    request = _request()
    with pytest.raises(Exception):
        ConsentRecovery.from_mapping(_recovery(request, **changes), request=request)
