from __future__ import annotations

import httpx

from kdcube_ai_app.apps.chat.sdk.integrations.provider_errors import (
    provider_failure_from_exception,
    provider_failure_from_payload,
    provider_failure_from_response,
)


def _response(status: int, payload: dict, **headers: str) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers=headers,
        request=httpx.Request("GET", "https://provider.example.test/resource"),
    )


def test_google_service_disabled_is_configuration_not_auth_failure():
    failure = provider_failure_from_response(
        _response(
            403,
            {
                "error": {
                    "code": 403,
                    "message": "Google Sheets API is disabled for this project.",
                    "status": "PERMISSION_DENIED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "SERVICE_DISABLED",
                        }
                    ],
                }
            },
        ),
        provider="google",
        service="google_sheets",
        operation="read",
        fallback="Google Sheets read failed.",
    )

    assert failure.category == "provider_configuration_error"
    assert failure.credential_failure is False
    assert failure.provider_status == 403
    assert failure.provider_code == "PERMISSION_DENIED"
    assert failure.provider_reason == "SERVICE_DISABLED"
    assert failure.normalized_code == "google_sheets_provider_configuration_error"


def test_scope_failure_is_credential_failure_but_generic_403_is_not():
    scope_failure = provider_failure_from_response(
        _response(
            403,
            {
                "error": {
                    "message": "Request had insufficient authentication scopes.",
                    "details": [
                        {"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
                    ],
                }
            },
        ),
        provider="google",
        service="gmail",
        operation="send_message",
        fallback="Gmail send failed.",
    )
    denied = provider_failure_from_response(
        _response(403, {"error": {"message": "Access denied by policy."}}),
        provider="google",
        service="gmail",
        operation="get_message",
        fallback="Gmail read failed.",
    )

    assert scope_failure.category == "scope_insufficient"
    assert scope_failure.credential_failure is True
    assert denied.category == "access_denied"
    assert denied.credential_failure is False


def test_scope_reason_embedded_in_provider_message_is_detected():
    failure = provider_failure_from_payload(
        {
            "error": {
                "status": "403",
                "message": "Not enough permissions to access this resource",
            }
        },
        provider_status=403,
        provider="linkedin",
        service="linkedin",
        operation="posts.create",
        fallback="LinkedIn post failed.",
    )

    assert failure.category == "scope_insufficient"
    assert failure.credential_failure is True


def test_slack_error_body_is_preserved_even_with_http_200():
    failure = provider_failure_from_payload(
        {"ok": False, "error": "invalid_auth"},
        provider_status=200,
        provider="slack",
        service="slack",
        operation="search.messages",
        fallback="Slack search failed.",
    )

    assert failure.credential_failure is True
    assert failure.provider_code == "invalid_auth"
    assert failure.provider_reason == "invalid_auth"
    assert failure.message == "invalid_auth"


def test_rate_limit_preserves_retry_after():
    failure = provider_failure_from_response(
        _response(429, {"error": "ratelimited"}, **{"Retry-After": "30"}),
        provider="slack",
        service="slack",
        operation="search.messages",
        fallback="Slack search failed.",
    )

    assert failure.category == "rate_limited"
    assert failure.retryable is True
    assert failure.client_ret()["retry_after"] == "30"


def test_mutating_transport_failure_has_unknown_outcome():
    request = httpx.Request("POST", "https://provider.example.test/write")
    failure = provider_failure_from_exception(
        httpx.ReadTimeout("timed out", request=request),
        provider="google",
        service="gmail",
        operation="send_message",
        fallback="Gmail did not return a response.",
        mutating=True,
    )

    result = failure.error_result(where="gmail.send_gmail")
    assert result["error"]["code"] == "gmail_transport_error"
    assert result["ret"]["provider_status"] == 0
    assert result["ret"]["retryable"] is True
    assert result["ret"]["outcome_unknown"] is True


def test_bearer_text_is_redacted_from_client_message():
    failure = provider_failure_from_payload(
        {"error": {"message": "failed for Bearer secret-token"}},
        provider_status=400,
        provider="example",
        service="example",
        operation="read",
        fallback="Provider request failed.",
    )

    assert failure.message == "failed for Bearer [REDACTED]"
    assert "secret-token" not in str(failure.error_result(where="example.read"))
