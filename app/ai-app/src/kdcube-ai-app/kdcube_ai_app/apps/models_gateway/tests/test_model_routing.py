# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.models_gateway.app import _to_ollama_request


def test_gateway_routes_request_model_and_context_to_ollama():
    body = _to_ollama_request(
        {
            "model": "mistral:7b-instruct-v0.2-q4_K_M",
            "inputs": [{"role": "user", "content": "hello"}],
            "parameters": {"num_ctx": 32768},
        },
        stream=True,
    )

    assert body["model"] == "mistral:7b-instruct-v0.2-q4_K_M"
    assert body["options"]["num_ctx"] == 32768
    assert body["stream"] is True
