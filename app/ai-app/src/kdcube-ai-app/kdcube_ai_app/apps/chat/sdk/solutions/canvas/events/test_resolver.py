from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import CanvasArtifactResolver


class _FakeArtifacts:
    def read(self, key: str) -> bytes:
        assert key == "canvas/users/user-1/files/report.html"
        return b"<html>Hello</html>"


class _FakeStore:
    tenant = "tenant"
    project = "project"
    bundle_id = "bundle@1"
    artifact_resolver_name = "canvas.bundle_artifact_storage"
    artifacts = _FakeArtifacts()


def test_canvas_artifact_download_returns_url_not_json_bytes():
    resolver = CanvasArtifactResolver(_FakeStore())  # type: ignore[arg-type]

    result = resolver.download_ref("cnv:canvas/users/user-1/files/report.html", mime="text/html")

    assert result["ok"] is True
    assert result["filename"] == "report.html"
    assert result["mime"] == "text/html"
    assert result["size"] == len(b"<html>Hello</html>")
    assert "content_base64" not in result
    assert result["download_url"].startswith(
        "/api/integrations/bundles/tenant/project/bundle%401/operations/canvas_object_download?"
    )
    assert "object_ref=cnv%3Acanvas%2Fusers%2Fuser-1%2Ffiles%2Freport.html" in result["download_url"]
