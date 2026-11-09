"""An exact ID read never turns into a Drive title listing or auth fallback."""
import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_proxy import run


@pytest.mark.parametrize("status", [200, 404, 403, 429])
def test_exact_lookup_uses_one_minimal_read_and_preserves_provider_failures(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/v1/presentations/deck"
        assert request.url.params["fields"] == "presentationId,title,revisionId"
        return httpx.Response(status, json={"presentationId": "deck", "title": "Exact", "revisionId": "r1"} if status == 200 else {"error": {"message": "Unavailable", "code": status}})
    result = run(monkeypatch, "search", {"presentation_id": "deck", "query": "must not search titles"}, handler)
    assert len(calls) == 1
    if status in (200, 404):
        assert result["ok"], result
        assert [row["presentation_id"] for row in result["ret"]["items"]] == (["deck"] if status == 200 else [])
    else:
        assert not result["ok"] and result["ret"]["provider_status"] == status
