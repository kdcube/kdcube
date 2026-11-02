"""The provider-file validation fetch keeps the browser-like caller shape."""
import asyncio
import io
from types import SimpleNamespace

import httpx
from PIL import Image

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_proxy import validate_image_uri
from kdcube_ai_app.apps.chat.sdk.tools.backends.web import ssrf_guard


def test_image_validation_uses_anonymous_browser_and_retains_ssrf_check(monkeypatch):
    stream = io.BytesIO()
    Image.new("RGB", (4, 2)).save(stream, "PNG")
    checked, requests = [], []

    async def allowed(url):
        checked.append(url)
        return SimpleNamespace(allowed=True)

    def origin(request):
        requests.append(request)
        if not request.headers["user-agent"].startswith("Mozilla/"):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"warning page")
        return httpx.Response(200, headers={"content-type": "image/png"}, content=stream.getvalue())

    monkeypatch.setattr(ssrf_guard, "check_url", allowed)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(origin)) as client:
            return await validate_image_uri(client, "https://origin.example/image")

    assert asyncio.run(run()) == {"width": 4, "height": 2}
    assert checked == ["https://origin.example/image"]
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers and "cookie" not in requests[0].headers
