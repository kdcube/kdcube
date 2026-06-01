# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import Timeline


class FakeBrowser:
    def __init__(self, runtime_ctx: RuntimeCtx):
        self.runtime_ctx = runtime_ctx
        self.timeline = Timeline(runtime=runtime_ctx, svc=None)
        self._turn_logs = {}

    def contribute(self, blocks, persist=True):
        self.timeline.blocks.extend(blocks or [])

    def contribute_notice(self, *, code, message, extra=None, call_id=None, meta=None):
        block = {
            "type": "react.notice",
            "call_id": call_id,
            "text": f"{code}:{message}",
            "meta": extra or {},
            "turn_id": self.runtime_ctx.turn_id or "",
        }
        if meta:
            block["meta"] = {**block.get("meta", {}), **meta}
        self.contribute([block])

    def timeline_artifacts(self, paths):
        return self.timeline.materialize_show_artifacts(paths)

    def hide_paths(self, *, paths, replacement):
        return self.timeline.hide_paths(paths, replacement)

    def unhide_paths(self, paths=None):
        return None

    def timeline_visible_paths(self):
        return self.timeline.visible_paths()

    def bind_params_with_refs(self, base_params, tool_id=None, visible_paths=None):
        return self.timeline.bind_params_with_refs(
            base_params=base_params,
            tool_id=tool_id,
            visible_paths=visible_paths,
        )

    async def get_turn_log(self, turn_id: str, conversation_id: str | None = None):
        return self._turn_logs.get((conversation_id or "", turn_id), self._turn_logs.get(turn_id, {}))


class FakeReact:
    tool_manager = type("T", (), {"tools": {}})()
    log = None

    def __init__(self, hosting_service=None, comm=None):
        self.hosting_service = hosting_service
        self.comm = comm
