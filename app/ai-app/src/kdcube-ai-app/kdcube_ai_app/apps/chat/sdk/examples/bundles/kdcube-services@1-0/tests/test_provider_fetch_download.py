"""The public provider-fetch route serves the signed media type, not a name.

A provider such as Google Docs fetches a staged file anonymously from this
deployment's public origin. The staged name comes from the caller, so the
route serves exactly the media type the staging action measured and signed,
always with nosniff, and serves anything a browser could execute as an
attachment.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import (
    new_staged_ref,
    save_staged,
)
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import (
    load_dynamic_module_for_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.download_links import (
    mint_file_download_token,
)

BUNDLE_ROOT = Path(__file__).resolve().parents[1]
SECRET = "fixture-provider-fetch-secret"


def _provider_fetch():
    _name, module = load_dynamic_module_for_path(
        BUNDLE_ROOT / "services" / "provider_fetch.py"
    )
    return module


def _png_with_html_tail() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, "PNG")
    return buffer.getvalue() + b"<html><script>1</script></html>"


def _fetch(root: Path, *, filename: str, media_type: str):
    ref = new_staged_ref(filename)
    save_staged(root, ref, _png_with_html_tail())
    token, _expires = mint_file_download_token(
        SECRET,
        fi_ref=ref,
        user_id="",
        include_identity=False,
        media_type=media_type,
    )
    return _provider_fetch().serve_provider_fetch(
        secret=SECRET, root=root, ref=ref, token=token
    )


def test_the_signed_type_is_served_whatever_the_staged_name(tmp_path) -> None:
    answer = _fetch(tmp_path, filename="page.html", media_type="image/png")

    assert answer.status == 200
    assert answer.media_type == "image/png"
    assert answer.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Disposition" not in answer.headers


def test_an_unsigned_type_is_served_as_an_opaque_attachment(tmp_path) -> None:
    answer = _fetch(tmp_path, filename="page.html", media_type="")

    assert answer.status == 200
    assert answer.media_type == "application/octet-stream"
    assert answer.headers["Content-Disposition"] == "attachment"
    assert answer.headers["X-Content-Type-Options"] == "nosniff"


def test_active_content_is_never_served_inline(tmp_path) -> None:
    for media_type in ("text/html", "image/svg+xml", "application/xml"):
        answer = _fetch(tmp_path, filename="page.png", media_type=media_type)

        assert answer.headers["Content-Disposition"] == "attachment", media_type
        assert answer.headers["X-Content-Type-Options"] == "nosniff", media_type


def test_a_token_for_another_ref_is_rejected(tmp_path) -> None:
    ref = new_staged_ref("image.png")
    save_staged(tmp_path, ref, _png_with_html_tail())
    token, _expires = mint_file_download_token(
        SECRET,
        fi_ref=new_staged_ref("image.png"),
        user_id="",
        include_identity=False,
        media_type="image/png",
    )

    answer = _provider_fetch().serve_provider_fetch(
        secret=SECRET, root=tmp_path, ref=ref, token=token
    )

    assert answer.status == 403
    assert answer.error["error"] == "fetch_token_rejected"
