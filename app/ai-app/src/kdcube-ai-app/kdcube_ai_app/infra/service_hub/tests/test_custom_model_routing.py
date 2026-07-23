# SPDX-License-Identifier: MIT

from types import SimpleNamespace

from kdcube_ai_app.infra.service_hub.inventory import Config, ConfigRequest, ModelRouter


def _router() -> ModelRouter:
    config = SimpleNamespace(
        custom_model_endpoint="http://models-gateway/generate",
        custom_model_api_key="",
        custom_model_num_ctx=65536,
        custom_model_overrides={
            "qwen3:8b": {"num_ctx": 40960},
            "mistral:7b-instruct-v0.2-q4_K_M": {"num_ctx": 32768},
        },
        default_llm_model={"provider": "anthropic", "model_name": "claude-sonnet-4-6"},
        ensure_role=lambda role: {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    )
    router = ModelRouter.__new__(ModelRouter)
    router.config = config
    router._cache = {}
    return router


def test_custom_model_config_has_no_competing_model_name():
    assert "custom_model_name" not in ConfigRequest.model_fields


def test_custom_client_routes_selected_model_and_uses_shared_context_fallback():
    client = _router()._mk_custom("qwen3.6:35b", 0.2)

    assert client.model_name == "qwen3.6:35b"
    assert client.default_params["num_ctx"] == 65536
    assert client._prepare_payload([])["model"] == "qwen3.6:35b"


def test_custom_client_applies_per_model_context_override():
    client = _router()._mk_custom("mistral:7b-instruct-v0.2-q4_K_M", 0.2)

    assert client.model_name == "mistral:7b-instruct-v0.2-q4_K_M"
    assert client.default_params["num_ctx"] == 32768
    assert client._prepare_payload([])["model"] == "mistral:7b-instruct-v0.2-q4_K_M"


def test_custom_client_applies_qwen_8b_context_override():
    client = _router()._mk_custom("qwen3:8b", 0.2)

    assert client.default_params["num_ctx"] == 40960


def test_picker_context_override_beats_model_and_service_defaults():
    client = _router()._mk_custom("qwen3:8b", 0.2, num_ctx=16384)

    assert client.default_params["num_ctx"] == 16384


def test_router_reads_picker_context_override_and_keys_cache_by_it():
    router = _router()
    requested = {
        "answer": {"provider": "custom", "model": "qwen3:8b", "num_ctx": 16384},
    }
    router._request_role_models = lambda: requested

    first = router.get_client("answer", 0.2)
    assert first.default_params["num_ctx"] == 16384

    requested["answer"] = {
        "provider": "custom", "model": "qwen3:8b", "num_ctx": 8192,
    }
    second = router.get_client("answer", 0.2)
    assert second.default_params["num_ctx"] == 8192
    assert second is not first


def test_custom_model_config_normalization_keeps_only_supported_overrides():
    config = Config.__new__(Config)

    config.set_custom_model_overrides({
        " mistral:7b-instruct-v0.2-q4_K_M ": {"num_ctx": "32768"},
        "empty": {},
        "invalid": {"num_ctx": "not-an-int"},
    })

    assert config.custom_model_overrides == {
        "mistral:7b-instruct-v0.2-q4_K_M": {"num_ctx": 32768},
    }
