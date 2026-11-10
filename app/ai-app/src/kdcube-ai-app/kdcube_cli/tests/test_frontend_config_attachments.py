# SPDX-License-Identifier: MIT
"""The browser config publishes the attachment caps the server enforces.

The chat client refuses a file over its cap in the picker; it can only agree
with the server when it receives the same platform.attachments values.
"""
from kdcube_cli.frontend_config import build_frontend_config


def _config(assembly):
    return build_frontend_config(tenant="t", project="p", assembly=assembly)


def test_absent_caps_publish_nothing():
    assert "attachments" not in _config({})


def test_descriptor_caps_are_published_in_browser_names():
    config = _config({"platform": {"attachments": {
        "max_image_bytes": 10485760,
        "max_doc_bytes": "20971520",
        "max_message_bytes": 52428800,
    }}})

    assert config["attachments"] == {
        "maxImageBytes": 10485760,
        "maxDocBytes": 20971520,
        "maxMessageBytes": 52428800,
    }


def test_malformed_caps_are_left_to_the_client_defaults():
    config = _config({"platform": {"attachments": {
        "max_image_bytes": "ten",
        "max_doc_bytes": 0,
        "max_message_bytes": True,
    }}})

    assert "attachments" not in config


def test_client_options_from_frontend_config_are_kept_and_caps_win():
    config = _config({
        "platform": {"attachments": {"max_image_bytes": 10485760}},
        "frontend": {"config": {"attachments": {"compressImages": True, "maxImageBytes": 1}}},
    })

    assert config["attachments"] == {"compressImages": True, "maxImageBytes": 10485760}
