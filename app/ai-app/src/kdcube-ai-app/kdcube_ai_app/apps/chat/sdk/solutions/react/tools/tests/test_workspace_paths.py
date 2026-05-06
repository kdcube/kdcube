# SPDX-License-Identifier: MIT

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import extract_code_file_paths


def test_extract_code_file_paths_is_artifact_segment_aware():
    code = (
        'current = "turn_13083704/outputs/email-attachments/acct/msg/invoice.pdf"\n'
        'relative = "outputs/email-attachments/acct/msg/receipt.pdf"\n'
        'user_file = "attachments/uploaded.pdf"\n'
        'url = "https://example.com/attachments/not-local.pdf"\n'
    )

    paths, rewritten = extract_code_file_paths(code, turn_id="turn_13083704")

    assert paths == []
    assert rewritten == [
        "turn_13083704/outputs/email-attachments/acct/msg/receipt.pdf",
        "turn_13083704/attachments/uploaded.pdf",
    ]
    assert "turn_13083704/attachments/acct/msg/invoice.pdf" not in rewritten
    assert "turn_13083704/attachments/not-local.pdf" not in rewritten
