# SPDX-License-Identifier: MIT

from kdcube_cli import cli


def _live(*, server_commit: str = "abc123", widget_commit: str | None = "abc123"):
    source = {
        "mode": "snapshot",
        "commit": server_commit,
        "ref": "main",
        "mounted_path": "/bundles/app",
        "path": f"/managed/{server_commit}",
    }
    widget = None
    if widget_commit is not None:
        widget = {
            "source": {
                **source,
                "commit": widget_commit,
                "path": f"/managed/{widget_commit}",
            }
        }
    return {
        "available": True,
        "process": {"loaded": {"source": source}},
        "widget": widget,
    }


def test_bundle_source_attestation_matches_descriptor_server_and_widget():
    result = cli._bundle_source_attestation(
        {
            "mode": "snapshot",
            "ref": "main",
            "commit": "abc123",
            "mounted_path": "/bundles/app",
        },
        _live(),
    )

    assert result["status"] == "MATCH"
    assert result["mismatch"] is False
    assert cli._bundle_status_exit_code({"attestation": result}) == 0


def test_bundle_source_attestation_names_widget_version_mismatch_and_fails():
    result = cli._bundle_source_attestation(
        {
            "mode": "snapshot",
            "ref": "main",
            "commit": "abc123",
            "mounted_path": "/bundles/app",
        },
        _live(widget_commit="def456"),
    )

    assert result["status"] == "MISMATCH"
    assert cli._bundle_status_exit_code({"attestation": result}) == 1
    assert any(
        item["status"] == "MISMATCH"
        and item["expected_field"] == "source.commit"
        and item["expected"] == "abc123"
        and item["actual"] == "def456"
        for item in result["comparisons"]
    )


def test_bundle_source_attestation_is_unknown_without_widget_receipt():
    result = cli._bundle_source_attestation(
        {"mode": "snapshot", "ref": "main", "commit": "abc123"},
        _live(widget_commit=None),
    )

    assert result["status"] == "UNKNOWN"
    assert cli._bundle_status_exit_code({"attestation": result}) == 0
