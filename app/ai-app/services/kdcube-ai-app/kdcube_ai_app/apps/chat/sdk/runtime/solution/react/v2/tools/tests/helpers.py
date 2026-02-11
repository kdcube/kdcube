# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.v2.timeline import Timeline


class FakeBrowser:
    def __init__(self, runtime_ctx: RuntimeCtx):
        self.runtime_ctx = runtime_ctx
        self.timeline = Timeline(runtime=runtime_ctx, svc=None)
        self._turn_logs = {}

    def contribute(self, blocks, persist=True):
        self.timeline.blocks.extend(blocks or [])

    def contribute_notice(self, *, code, message, extra=None, call_id=None):
        self.contribute([{
            "type": "react.notice",
            "call_id": call_id,
            "text": f"{code}:{message}",
            "meta": extra or {},
            "turn_id": self.runtime_ctx.turn_id or "",
        }])

    def timeline_artifacts(self, paths):
        return self.timeline.timeline_artifacts(paths)

    def unhide_paths(self, paths=None):
        return None

    def bind_params_with_refs(self, base_params, tool_id=None, visible_paths=None):
        return base_params, [], []

    async def get_turn_log(self, turn_id: str):
        return self._turn_logs.get(turn_id, {})


class FakeReact:
    hosting_service = None
    comm = None
    tool_manager = type("T", (), {"tools": {}})()
    log = None
