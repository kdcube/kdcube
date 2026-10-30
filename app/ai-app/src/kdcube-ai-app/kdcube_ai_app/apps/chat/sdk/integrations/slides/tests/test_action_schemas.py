"""Composition payloads are discoverable without reading implementation files."""
import asyncio
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.slides.named_service import make_slides_named_service_provider
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext, NamedServiceRequest


@pytest.mark.parametrize("action", ["compose", "add_shape", "add_image"])
def test_action_discovery_exposes_structured_payload_without_provider_access(action):
    async def execute(**kwargs):
        raise AssertionError("Schema discovery must stay local")
    provider = make_slides_named_service_provider(execute_operation=execute)
    request = NamedServiceRequest(operation="object.schema", namespace="slides", object_kind="slides.presentation", schema_operation="object.action:" + action)
    response = asyncio.run(provider.dispatch(NamedServiceContext(), request))
    assert response.ok
    contract = response.extra["schema"]["actions"][action]
    payload = contract["payload_schema"]
    assert payload["type"] == "object" and payload["additionalProperties"] is False
    assert {"slide", "preview", "revision_id"} <= set(contract["payload"])
    assert "slide" in payload["properties"]
    json.dumps(payload)  # Recursive grids use references, not Python cycles.
    if action == "compose":
        assert payload["required"] == ["cells"]
        element = payload["properties"]["cells"]["items"]["properties"]["element"]
        assert {choice["$ref"] for choice in element["oneOf"]} == {"#/$defs/element_selector", "#/$defs/shape", "#/$defs/image"}
        assert {"text", "items", "shape", "fill", "outline", "color"} <= set(payload["$defs"]["shape"]["properties"])
        assert {"image_uri", "file_ref", "image"} <= set(payload["$defs"]["image"]["properties"])
        assert payload["$defs"]["grid"]["properties"]["cells"]["maxItems"] == 100
    elif action == "add_shape":
        assert payload["properties"]["shape"]["enum"] == ["text_box", "rectangle", "round_rectangle", "ellipse"]
        assert "theme.accent1" in payload["properties"]["fill"]["enum"]
        assert "color" in payload["properties"]
    else:
        assert len(payload["oneOf"]) == 3
        assert {"image_uri", "file_ref", "image", "element", "region"} <= set(payload["properties"])


@pytest.mark.parametrize("action,payload", [
    ("compose", {"cols": [1, 2], "cells": [{"at": [0, 0], "element": {"shape": "rectangle", "outline": "theme.accent1"}}, {"at": [0, 1], "grid": {"cells": [{"at": [0, 0], "element": {"image_uri": "https://images.example/chart.png"}}]}}]}),
    ("add_shape", {"below": {"role": "title"}, "text": "Caption", "color": "theme.light1"}),
    ("add_image", {"file_ref": "staged:00000000000000000000000000000000:image.png", "element": {"role": "body"}}),
])
def test_discovered_payload_schemas_accept_supported_content(action, payload):
    from jsonschema import Draft202012Validator
    from kdcube_ai_app.apps.chat.sdk.integrations.slides.schema import SLIDES_SCHEMA
    schema = SLIDES_SCHEMA["actions"][action]["payload_schema"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize("action,payload", [
    ("compose", {"cols": [0], "cells": [{"at": [0, 0], "element": {"text": "One"}}]}),
    ("compose", {"cells": [{"at": [0, 0], "element": {"text": "One"}, "grid": {"cells": []}}]}),
    ("add_shape", {"text": "One", "items": ["Two"]}),
    ("add_image", {"image_uri": "https://images.example/a.png", "file_ref": "another-source"}),
])
def test_discovered_payload_schemas_reject_ambiguous_or_invalid_content(action, payload):
    from jsonschema import Draft202012Validator
    from kdcube_ai_app.apps.chat.sdk.integrations.slides.schema import SLIDES_SCHEMA
    assert list(Draft202012Validator(SLIDES_SCHEMA["actions"][action]["payload_schema"]).iter_errors(payload))
