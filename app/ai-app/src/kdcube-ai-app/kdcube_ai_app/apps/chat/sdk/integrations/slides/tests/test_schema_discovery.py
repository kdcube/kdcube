"""Budgeted callers discover one capability contract instead of the whole schema."""
import asyncio
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.slides.named_service import make_slides_named_service_provider
from kdcube_ai_app.apps.chat.sdk.integrations.slides.schema import SLIDES_ACTIONS
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext, NamedServiceRequest


def discover(**kwargs):
    async def no_google(**_):
        raise AssertionError("Schema discovery must not resolve a Google account")
    provider = make_slides_named_service_provider(execute_operation=no_google)
    response = asyncio.run(provider.dispatch(NamedServiceContext(), NamedServiceRequest(operation="object.schema", namespace="slides", **kwargs)))
    assert response.ok, response.to_dict()
    return response.extra["schema"]


def test_default_discovery_is_a_small_recursive_catalog():
    schema = discover()
    assert schema["schema_projection"]["view"] == "catalog"
    assert [child["path"] for child in schema["catalog"]["children"]] == ["/reading", "/building", "/text", "/layout", "/tables", "/slides", "/comments", "/lifecycle"]
    assert "actions" not in schema
    assert len(json.dumps(schema)) < 25000


@pytest.mark.parametrize("action", SLIDES_ACTIONS)
def test_each_action_has_one_focused_executable_contract(action):
    schema = discover(object_kind="slides.presentation", schema_operation="object.action:" + action)
    assert list(schema["actions"]) == [action]
    assert schema["actions"][action]["description"]


def test_full_schema_is_explicit_and_path_read_does_not_load_action_payloads():
    full = discover(schema_view="full")
    branch = discover(schema_path="/tables")
    assert set(full["actions"]) == set(SLIDES_ACTIONS)
    assert branch["schema_projection"]["schema_path"] == "/tables"
    assert "actions" not in branch
    assert len(json.dumps(branch)) < len(json.dumps(full)) / 3


@pytest.mark.parametrize("kind", ["slides.slide", "slides.export", "slides.thumbnail"])
def test_related_kinds_have_a_focused_read_contract(kind):
    schema = discover(object_kind=kind, schema_operation="object.get")
    assert schema["schema_projection"]["object_kind"] == kind
    assert list(schema["operations"]) == ["object.get"]
