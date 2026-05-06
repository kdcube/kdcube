import logging

from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import add_block, tool_call_block


class _RuntimeCtx:
    turn_id = "turn_test"


class _Ctx:
    runtime_ctx = _RuntimeCtx()

    def __init__(self):
        self.blocks = []

    def contribute(self, *, blocks):
        self.blocks.extend(blocks)


def test_tool_call_block_logs_payload(caplog):
    ctx = _Ctx()
    with caplog.at_level(logging.INFO, logger="kdcube.react.artifacts"):
        tool_call_block(
            ctx_browser=ctx,
            tool_call_id="tc_test",
            tool_id="email.process_user_emails",
            payload={"account": "lena@nestlogic.com", "notes": "do not log notes"},
        )

    assert len(ctx.blocks) == 1
    assert "[react.tool.call]" in caplog.text
    assert "turn_id=turn_test" in caplog.text
    assert "call_id=tc_test" in caplog.text
    assert "tool_id=email.process_user_emails" in caplog.text
    assert '"account": "lena@nestlogic.com"' in caplog.text
    assert "do not log notes" not in caplog.text


def test_tool_result_block_logs_text_payload(caplog):
    ctx = _Ctx()
    with caplog.at_level(logging.INFO, logger="kdcube.react.artifacts"):
        add_block(ctx, {
            "turn": "turn_test",
            "type": "react.tool.result",
            "call_id": "tc_test",
            "mime": "application/json",
            "path": "tc:turn_test.tc_test.result",
            "text": '{"ok": true, "file_count": 20}',
            "meta": {"tool_call_id": "tc_test"},
        })

    assert len(ctx.blocks) == 1
    assert "[react.tool.result]" in caplog.text
    assert "turn_id=turn_test" in caplog.text
    assert "call_id=tc_test" in caplog.text
    assert '{"ok": true, "file_count": 20}' in caplog.text


def test_tool_result_binary_block_omits_base64(caplog):
    ctx = _Ctx()
    with caplog.at_level(logging.INFO, logger="kdcube.react.artifacts"):
        add_block(ctx, {
            "turn": "turn_test",
            "type": "react.tool.result",
            "call_id": "tc_test",
            "mime": "application/pdf",
            "path": "fi:turn_test.outputs/invoice.pdf",
            "base64": "a" * 1024,
            "meta": {"tool_call_id": "tc_test", "physical_path": "turn_test/outputs/invoice.pdf"},
        })

    assert "[react.tool.result]" in caplog.text
    assert "application/pdf" in caplog.text
    assert "<omitted 1024 chars>" in caplog.text
    assert "aaaaaaaaaaaaaaaa" not in caplog.text
