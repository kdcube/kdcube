from types import SimpleNamespace

from kdcube_ai_app.apps.utils.cors import _cors_origin_options


def test_cors_origin_options_convert_preview_globs_to_regex():
    exact, regex = _cors_origin_options(
        SimpleNamespace(
            allow_origins=[
                "https://dev.kdcube.tech",
                "https://*.kdcube.tech",
            ],
            allow_origin_regex=None,
        )
    )

    assert exact == ["https://dev.kdcube.tech"]
    assert regex is not None
    assert "https://[^/]*\\.kdcube\\.tech" in regex


def test_cors_origin_options_preserve_explicit_regex():
    exact, regex = _cors_origin_options(
        SimpleNamespace(
            allow_origins=["https://kdcube.tech"],
            allow_origin_regex=r"^https://preview-[0-9]+\.example\.com$",
        )
    )

    assert exact == ["https://kdcube.tech"]
    assert regex == r"(?:^https://preview-[0-9]+\.example\.com$)"
