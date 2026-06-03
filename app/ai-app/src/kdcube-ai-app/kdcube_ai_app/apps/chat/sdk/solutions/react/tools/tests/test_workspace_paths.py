# SPDX-License-Identifier: MIT

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import extract_code_file_paths


def test_extract_code_file_paths_is_artifact_segment_aware():
    code = (
        'current = "turn_13083704/outputs/email-attachments/acct/msg/invoice.pdf"\n'
        'current_external = "turn_13083704/external/followup/attachments/msg/evidence.png"\n'
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


def test_extract_code_file_paths_preserves_timestamp_turn_prefix():
    turn_id = "turn_2026-05-19-01-01-49-177"
    code = (
        f'out_path = Path(OUTPUT_DIR) / "{turn_id}/outputs/science_news/top3.xlsx"\n'
        'old_path = "turn_2026-05-18-01-01-49-177/files/input.csv"\n'
    )

    paths, rewritten = extract_code_file_paths(code, turn_id=turn_id)

    assert rewritten == []
    assert paths == ["turn_2026-05-18-01-01-49-177/files/input.csv"]


def test_extract_code_file_paths_preserves_cross_conversation_scope():
    cross_conv_path = (
        "conv_81920790-790d-479e-9c5c-ec407d6298d3/"
        "turn_2026-05-26-16-29-44-474/"
        "outputs/science_news/top3_science_news.pdf"
    )
    code = f'src = Path(OUTPUT_DIR) / "{cross_conv_path}"\n'

    paths, rewritten = extract_code_file_paths(code, turn_id="turn_2026-06-03-01-28-10-242")

    assert rewritten == []
    assert paths == [cross_conv_path]
