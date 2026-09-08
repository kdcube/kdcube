from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting import model_service


def _plain(values: dict[str, object]):
    return lambda key, default=None: values.get(key, default)


def test_explicit_provider_accepts_an_arbitrary_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_service,
        "get_settings",
        lambda: SimpleNamespace(DEFAULT_MODEL_LLM_ID="operator:model", TENANT="t", PROJECT="p"),
    )
    monkeypatch.setattr(
        model_service,
        "get_plain",
        _plain({"a:models.default_llm_provider": "custom"}),
    )

    selected = model_service.configured_model_selection()

    assert selected == model_service.DirectModelSelection(
        provider="custom",
        model="operator:model",
    )


def test_custom_model_request_projects_endpoint_and_shared_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_service,
        "get_settings",
        lambda: SimpleNamespace(DEFAULT_MODEL_LLM_ID="operator:model", TENANT="t", PROJECT="p"),
    )
    monkeypatch.setattr(
        model_service,
        "get_plain",
        _plain(
            {
                "a:models.default_llm_provider": "custom",
                "a:services.llm.custom": {
                    "endpoint": "http://127.0.0.1:11500/generate",
                    "num_ctx": 24576,
                },
            }
        ),
    )

    request = model_service._request_from_descriptors(role="demo.agent")

    assert request.role_models == {
        "demo.agent": {"provider": "custom", "model": "operator:model"}
    }
    assert request.custom_model_endpoint == "http://127.0.0.1:11500/generate"
    assert request.custom_model_num_ctx == 24576
    assert request.custom_model_overrides is None
    assert request.tenant == "t"
    assert request.project == "p"


def test_registered_model_keeps_backward_compatible_provider_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_service,
        "get_settings",
        lambda: SimpleNamespace(DEFAULT_MODEL_LLM_ID="gpt-4o"),
    )
    monkeypatch.setattr(model_service, "get_plain", _plain({}))

    assert model_service.configured_model_selection() == (
        model_service.DirectModelSelection(provider="openai", model="gpt-4o")
    )


@pytest.mark.parametrize(
    ("provider", "field"),
    (
        ("openai", "openai_api_key"),
        ("anthropic", "claude_api_key"),
        ("google", "google_api_key"),
        ("hugging-face", "huggingface_api_key"),
        ("openrouter", "openrouter_api_key"),
    ),
)
def test_embedding_service_requires_selected_provider_credential(
    provider: str,
    field: str,
) -> None:
    config = SimpleNamespace(embedder_config={"provider": provider})
    service = SimpleNamespace(config=config)

    assert model_service.embedding_service_if_configured(service) is None

    setattr(config, field, "configured-secret")
    assert model_service.embedding_service_if_configured(service) is service


def test_custom_embedding_service_requires_endpoint() -> None:
    config = SimpleNamespace(
        embedder_config={"provider": "custom"},
        custom_embedding_endpoint=None,
    )
    service = SimpleNamespace(config=config)

    assert model_service.embedding_service_if_configured(service) is None

    config.custom_embedding_endpoint = "http://127.0.0.1:8080/embed"
    assert model_service.embedding_service_if_configured(service) is service


def test_unknown_model_without_provider_fails_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_service,
        "get_settings",
        lambda: SimpleNamespace(DEFAULT_MODEL_LLM_ID="operator:model"),
    )
    monkeypatch.setattr(model_service, "get_plain", _plain({}))

    with pytest.raises(ValueError, match="default_llm_provider explicitly"):
        model_service.configured_model_selection()


@pytest.mark.parametrize("num_ctx", (0, -1, "not-an-integer"))
def test_custom_context_budget_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
    num_ctx: object,
) -> None:
    monkeypatch.setattr(
        model_service,
        "get_settings",
        lambda: SimpleNamespace(DEFAULT_MODEL_LLM_ID="operator:model"),
    )
    monkeypatch.setattr(
        model_service,
        "get_plain",
        _plain(
            {
                "a:models.default_llm_provider": "custom",
                "a:services.llm.custom": {
                    "endpoint": "http://127.0.0.1:11500/generate",
                    "num_ctx": num_ctx,
                },
            }
        ),
    )

    with pytest.raises(ValueError, match="num_ctx must be a positive integer"):
        model_service._request_from_descriptors(role="demo.agent")
