"""Compare canonical arguments with the real Hub method; storage ports are fake."""
from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.automation_access import AutomationAccessService
from connection_hub.delegated_credentials.oauth_issuance import original_input_digest
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_candidate_inputs import oauth_issuance_arguments
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import OriginalExchangeRefused


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{}, {"scopes": ("records:read",), "operations": ("records.read",)},
    {"replace_authority": False, "properties": {}, "expected_card_revision": 7,
     "resource_grants": {"/records": ["records:read"]}, "client_metadata": {"label": "unit"}}])
async def test_argument_digest_matches_real_hub_begin_defaults(changes):
    inputs = {"grantor_subject": "human", "client_id": "unit-client", **changes}
    normalized = oauth_issuance_arguments(inputs)
    captured = SimpleNamespace()
    class Store:
        async def read_issuance_plan_request(self, request):
            return None
        async def put_issuance_plan(self, **value):
            return {"plan": value["plan"]}
    service = object.__new__(AutomationAccessService)
    service._issuance_parts = lambda: (object(), object(), object(), 60, Store())
    async def plan(**value):
        captured.digest, captured.arguments = value["input_digest"], value["record_inputs"]
        return {"reserved_until": 2000000000}
    async def begin(plan, **kwargs):
        return plan
    service._plan_oauth_issuance, service._begin_planned_issuance = plan, begin
    await service.begin_oauth_issuance(original_request_id="unit-original", **inputs)
    assert captured.digest == original_input_digest(normalized)
    assert captured.arguments == {key: value for key, value in normalized.items()
                                   if key not in {"grantor_subject", "client_id"}}


def test_argument_normalization_freezes_nested_host_input():
    raw = {"grantor_subject": "human", "client_id": "unit-client",
           "resource_grants": {"/records": ["records:read"]}}
    normalized = oauth_issuance_arguments(raw)
    raw["resource_grants"]["/records"].append("records:write")
    assert normalized["resource_grants"] == {"/records": ["records:read"]}
    assert normalized == oauth_issuance_arguments(normalized)


@pytest.mark.parametrize("changes", [{"original_request_id": "caller-selected"}, {"unknown": True},
    {"grantor_subject": ""}, {"client_id": " padded "}, {"replace_authority": "yes"},
    {"scopes": "records:read"}, {"operations": "records.read"}])
def test_argument_normalization_refuses_unbound_or_ambiguous_inputs(changes):
    with pytest.raises(OriginalExchangeRefused):
        oauth_issuance_arguments({"grantor_subject": "human", "client_id": "unit-client", **changes})
