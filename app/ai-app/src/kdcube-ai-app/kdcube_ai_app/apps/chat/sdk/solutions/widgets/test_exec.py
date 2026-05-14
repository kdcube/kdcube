import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.widgets.exec import DecisionExecCodeStreamer


async def _noop_delta(**_kwargs):
    return None


@pytest.mark.asyncio
async def test_exec_widget_treats_unqualified_outputs_contract_as_complete():
    widget = DecisionExecCodeStreamer(
        emit_delta=_noop_delta,
        agent="test.agent",
        artifact_name="react.exec.test",
        turn_id="turn_123",
    )
    decision = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "exec_tools.execute_code_python",
            "params": {
                "contract": [
                    {
                        "filename": "outputs/report.xlsx",
                        "description": "Excel report",
                        "visibility": "external",
                    }
                ],
                "prog_name": "build_report",
            },
        },
    }

    await widget.feed_json(json.dumps(decision), completed=True)
    await widget.feed_code("print('ok')\n", completed=True)

    assert widget.has_contract()
    assert widget.is_complete()
    assert widget.get_code() == "print('ok')\n"
    assert widget.pending_contract["report"]["filename"].endswith("outputs/report.xlsx")


@pytest.mark.asyncio
async def test_exec_widget_preserves_code_chunk_sent_with_completed_signal():
    widget = DecisionExecCodeStreamer(
        emit_delta=_noop_delta,
        agent="test.agent",
        artifact_name="react.exec.test",
        turn_id="turn_123",
    )

    await widget.feed_code("print('last chunk')\n", completed=True)

    assert widget.get_code() == "print('last chunk')\n"
