# SPDX-License-Identifier: MIT

"""Named-service client policy travels into the exec child.

Surfaced case: `named_services.object_action` on mail passed when the ReAct
loop called it directly but failed with `named_service_tool_not_allowed_for_client`
from generated code — the exec child's registry carried no bundle props or
client id, so the policy collapsed to the read-only defaults.
"""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.runtime.bootstrap import make_registry
from kdcube_ai_app.apps.chat.sdk.runtime.portable_spec import ModelConfigSpec, PortableSpec


def test_portable_spec_carries_named_service_policy_into_child_registry():
    context = {
        "client_id": "default.react.agent",
        "bundle_props": {
            "surfaces": {
                "as_consumer": {
                    "agents": {
                        "main": {
                            "tools": [
                                {
                                    "kind": "named_service",
                                    "namespaces": {"mail": {"allowed": ["*"]}},
                                }
                            ]
                        }
                    }
                }
            }
        },
    }
    spec = PortableSpec(model_config=ModelConfigSpec(), named_services_context=context)

    # The spec round-trips through JSON (that is how it reaches the child).
    restored = PortableSpec.from_json(spec.to_json())
    registry = make_registry(restored)

    assert registry["client_id"] == "default.react.agent"
    assert registry["bundle_props"] == context["bundle_props"]


def test_child_registry_stays_lean_without_named_service_context():
    spec = PortableSpec(model_config=ModelConfigSpec())
    restored = PortableSpec.from_json(spec.to_json())
    registry = make_registry(restored)
    assert "bundle_props" not in registry
    assert "client_id" not in registry


def test_portable_spec_preserves_custom_model_serving_overrides():
    spec = PortableSpec(
        model_config=ModelConfigSpec(
            custom_model_num_ctx=65536,
            custom_model_overrides={
                "qwen3:8b": {"num_ctx": 40960},
                "mistral:7b-instruct-v0.2-q4_K_M": {"num_ctx": 32768},
            },
        )
    )

    restored = PortableSpec.from_json(spec.to_json())

    assert restored.model_config.custom_model_num_ctx == 65536
    assert restored.model_config.custom_model_overrides == {
        "qwen3:8b": {"num_ctx": 40960},
        "mistral:7b-instruct-v0.2-q4_K_M": {"num_ctx": 32768},
    }
