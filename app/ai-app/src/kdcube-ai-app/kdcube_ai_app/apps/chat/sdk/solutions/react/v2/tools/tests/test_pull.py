# SPDX-License-Identifier: MIT

import base64
import json
from datetime import datetime, timezone
from types import ModuleType

import pytest

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem, artifact_namespace_rehoster
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace import resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace.references import (
    build_physical_artifact_path,
    physical_path_to_logical_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.pull import handle_react_pull
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser


def _latest_payload(ctx: FakeBrowser) -> dict:
    blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("mime") == "application/json"
    ]
    assert blocks
    return json.loads(blocks[-1]["text"])


@pytest.mark.asyncio
async def test_pull_materializes_turn_file_subtree_from_fi_paths(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"conv:fi:turn_prev.git/projects/projectA/settings.json","physical_path":"turn_prev/git/projects/projectA/settings.json"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "path": "conv:fi:turn_prev.git/projects/projectA/settings.json",
                "text": '{"theme": "dark"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": '{"artifact_path":"conv:fi:turn_prev.git/projects/projectA/src/app.py","physical_path":"turn_prev/git/projects/projectA/src/app.py"}',
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "text/x-python",
                "path": "conv:fi:turn_prev.git/projects/projectA/src/app.py",
                "text": 'print("ok")\n',
                "turn_id": "turn_prev",
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["conv:fi:turn_prev.git/projects/projectA"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_files")

    payload = _latest_payload(ctx)
    assert payload["pulled"][0]["logical_root"] == "conv:fi:turn_prev.git/projects/projectA"
    assert payload["pulled"][0]["physical_root"] == "turn_prev/git/projects/projectA"
    assert payload["pulled"][0]["file_count"] == 2
    assert "settings.json" in payload["pulled"][0]["tree"]
    assert "src/" in payload["pulled"][0]["tree"]
    assert "app.py" in payload["pulled"][0]["tree"]
    assert payload["pulled"][0]["path_rule"] == {
        "logical": "conv:fi:turn_prev.git/projects/projectA/<path shown in tree>",
        "physical": "turn_prev/git/projects/projectA/<path shown in tree>",
    }
    assert "invalid" not in payload
    assert "missing" not in payload
    assert "errors" not in payload
    artifact_root = outdir / "workdir"
    assert (artifact_root / "turn_prev" / "git" / "projects" / "projectA" / "settings.json").read_text(encoding="utf-8") == '{"theme": "dark"}'
    assert (artifact_root / "turn_prev" / "git" / "projects" / "projectA" / "src" / "app.py").read_text(encoding="utf-8") == 'print("ok")\n'


@pytest.mark.asyncio
async def test_pull_materializes_registered_namespace_ref(tmp_path):
    outdir = tmp_path / "out"

    @artifact_namespace_rehoster(namespace="nmsp")
    async def rehost_nmsp_ref(*, ref, key, ctx_browser, outdir, **_):
        turn_id = ctx_browser.runtime_ctx.turn_id
        physical_path = build_physical_artifact_path(
            turn_id=turn_id,
            namespace="git/snapshots",
            relpath=f"nmsp/{key}",
        )
        target = resolve_artifact_path(outdir, physical_path, prefer_existing=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("status: draft\n", encoding="utf-8")
        return {
            "materialized": [{
                "object_ref": ref,
                "logical_path": physical_path_to_logical_path(physical_path),
                "physical_path": physical_path,
                "scope": "git/snapshots",
                "metadata": {
                    "updated_at": datetime(2026, 6, 12, 20, 39, tzinfo=timezone.utc),
                },
            }]
        }

    mod = ModuleType("nmsp_rehosters")
    mod.rehost_nmsp_ref = rehost_nmsp_ref
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    runtime.event_sources = EventSourceSubsystem(modules=[{"mod": mod}])
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["nmsp:draft_1/issue-draft.yaml"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_case")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == [{
        "object_ref": "nmsp:draft_1/issue-draft.yaml",
        "logical_path": "conv:fi:turn_pull.git/snapshots/nmsp/draft_1/issue-draft.yaml",
        "physical_path": "turn_pull/git/snapshots/nmsp/draft_1/issue-draft.yaml",
        "scope": "git/snapshots",
        "metadata": {
            "updated_at": "2026-06-12T20:39:00+00:00",
        },
        "file_count": 1,
    }]
    assert "invalid" not in payload
    assert "missing" not in payload
    assert "errors" not in payload
    assert state["pulled_object_refs"]["nmsp:draft_1/issue-draft.yaml"]["logical_path"] == "conv:fi:turn_pull.git/snapshots/nmsp/draft_1/issue-draft.yaml"
    assert state["pulled_logical_refs"]["conv:fi:turn_pull.git/snapshots/nmsp/draft_1/issue-draft.yaml"]["object_ref"] == "nmsp:draft_1/issue-draft.yaml"
    assert (outdir / "workdir" / "turn_pull" / "git" / "snapshots" / "nmsp" / "draft_1" / "issue-draft.yaml").read_text(encoding="utf-8") == "status: draft\n"


@pytest.mark.asyncio
async def test_pull_materializes_canvas_owned_attachment_ref(tmp_path, monkeypatch):
    outdir = tmp_path / "out"
    object_ref = (
        "cnv:canvas/users/user-1/canvases/cnv_user-1_main/"
        "objects/user-attachments/ua_2026-06-09-18-38-30_xkib/v000001.docx"
    )
    storage_key = object_ref.split(":", 1)[1]

    from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events import resolver as canvas_resolver

    class _Artifacts:
        def read(self, key):
            assert key == storage_key
            return b"DOCXDATA"

    class _Store:
        artifacts = _Artifacts()

    monkeypatch.setattr(canvas_resolver, "_store_from_runtime", lambda runtime: _Store())

    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    runtime.event_sources = EventSourceSubsystem(
        modules=[
            {
                "mod": canvas_resolver,
                "alias": "canvas",
            }
        ]
    )
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": [object_ref],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_canvas_docx")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == [{
        "object_ref": object_ref,
        "logical_path": "conv:fi:turn_pull.user.attachments/cnv/canvas/users/user-1/canvases/cnv_user-1_main/objects/user-attachments/ua_2026-06-09-18-38-30_xkib/v000001.docx",
        "physical_path": "turn_pull/attachments/cnv/canvas/users/user-1/canvases/cnv_user-1_main/objects/user-attachments/ua_2026-06-09-18-38-30_xkib/v000001.docx",
        "scope": "attachments",
        "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size_bytes": 8,
        "file_count": 1,
    }]
    assert "invalid" not in payload
    assert "missing" not in payload
    assert "errors" not in payload
    target = (
        outdir
        / "workdir"
        / "turn_pull"
        / "attachments"
        / "cnv"
        / "canvas"
        / "users"
        / "user-1"
        / "canvases"
        / "cnv_user-1_main"
        / "objects"
        / "user-attachments"
        / "ua_2026-06-09-18-38-30_xkib"
        / "v000001.docx"
    )
    assert target.read_bytes() == b"DOCXDATA"


@pytest.mark.asyncio
async def test_pull_materializes_exact_attachment_ref(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)
    payload = base64.b64encode(b"XLSXDATA").decode("utf-8")
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "path": "conv:fi:turn_prev.user.attachments/template.xlsx",
                "base64": payload,
                "turn_id": "turn_prev",
                "meta": {
                    "physical_path": "turn_prev/attachments/template.xlsx",
                    "size_bytes": 8,
                },
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["conv:fi:turn_prev.user.attachments/template.xlsx"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_attachment")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == [{
        "logical_path": "conv:fi:turn_prev.user.attachments/template.xlsx",
        "physical_path": "turn_prev/attachments/template.xlsx",
        "file_count": 1,
    }]
    assert "invalid" not in payload
    assert "missing" not in payload
    assert "errors" not in payload
    assert (outdir / "workdir" / "turn_prev" / "attachments" / "template.xlsx").read_bytes() == b"XLSXDATA"


@pytest.mark.asyncio
async def test_pull_materializes_hosted_internal_output_not_preview(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)
    physical = "turn_prev/files/analysis/zip_contents.json"
    logical = "conv:fi:turn_prev.files/analysis/zip_contents.json"
    hosted_key = (
        "cb/tenants/demo/projects/demo/attachments/user-1/conv-1/"
        "turn_prev/turn_prev/files/analysis/zip_contents.json"
    )
    full_payload = b'{"full": true, "items": [1, 2, 3]}\n'
    blob = tmp_path / hosted_key
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(full_payload)
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "text": json.dumps({
                    "artifact_path": logical,
                    "physical_path": physical,
                    "mime": "application/json",
                    "kind": "file",
                    "visibility": "internal",
                    "size_bytes": len(full_payload),
                    "key": hosted_key,
                }),
                "turn_id": "turn_prev",
            },
            {
                "type": "react.tool.result",
                "mime": "application/json",
                "path": logical,
                "text": "[TEXT FILE PREVIEW]\ntruncated preview, not the artifact bytes\n",
                "turn_id": "turn_prev",
                "meta": {
                    "physical_path": physical,
                    "key": hosted_key,
                    "size_bytes": len(full_payload),
                },
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": [logical],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_internal_output")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == [{
        "logical_path": logical,
        "physical_path": physical,
        "file_count": 1,
    }]
    assert "missing" not in payload
    assert "errors" not in payload
    assert (outdir / "workdir" / physical).read_bytes() == full_payload


@pytest.mark.asyncio
async def test_pull_rejects_attachment_prefix_pull(tmp_path):
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["conv:fi:turn_prev.user.attachments/binaries"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_bad_attachment")

    payload = _latest_payload(ctx)
    assert payload["pulled"] == []
    assert payload["invalid"] == [{
        "path": "conv:fi:turn_prev.user.attachments/binaries",
        "reason": "attachment_pulls_require_exact_file_ref",
    }]
    assert "missing" not in payload
    assert "errors" not in payload


@pytest.mark.asyncio
async def test_pull_share_that_resolves_nothing_surfaces_the_real_problem(tmp_path):
    """Regression: a share=true pull that resolved to nothing reported the
    outcome as a bare `pulled: []`, so neither the model nor the UI step could
    tell the share had failed or why. The result must name the failed share and
    the unresolved ref, in-band, and last_tool_result must carry that diagnostic
    (not an empty list) so the model receives the feedback too."""
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["conv:fi:turn_prev.user.attachments/binaries"],
                    "share": True,
                }
            }
        },
        "outdir": str(outdir),
    }

    # A hosting surface is present (react is not None); the share still cannot
    # apply because nothing resolved to an exact shareable file.
    out = await handle_react_pull(
        react=object(), ctx_browser=ctx, state=state, tool_call_id="pull_share_empty"
    )

    # Model-facing: the tool-result payload names the failed share and the reason.
    payload = _latest_payload(ctx)
    assert payload["pulled"] == []
    assert payload["share"]["delivered"] is False
    assert payload["share"]["reason"] == "share_single_file_only"
    assert payload["share"]["shareable_count"] == 0
    assert payload["user_delivery"].startswith("none")
    assert payload["invalid"]

    # A notice also names the failed share so it is not silently dropped.
    assert any(
        b.get("type") == "react.notice"
        and "share_single_file_only" in (b.get("text") or "")
        for b in ctx.timeline.blocks
    )

    # UI/runtime-facing: last_tool_result is the diagnostic payload, not `[]`.
    assert isinstance(out["last_tool_result"], dict)
    assert out["last_tool_result"]["invalid"]
    assert out["last_tool_result"]["share"]["delivered"] is False


@pytest.mark.asyncio
async def test_pull_without_share_names_the_share_fix_in_user_delivery(tmp_path):
    """Regression: a pull without share materializes locally only; the model
    used to claim the files were delivered ("in the Files tab") and the user
    saw nothing. The result now states what the user received and names the
    fix (share=true on the exact file ref)."""
    outdir = tmp_path / "out"
    runtime = RuntimeCtx(turn_id="turn_pull", outdir=str(outdir), workdir=str(tmp_path / "work"))
    ctx = FakeBrowser(runtime)
    payload = base64.b64encode(b"SVGDATA").decode("utf-8")
    ctx._turn_logs["turn_prev"] = {
        "blocks": [
            {
                "type": "react.tool.result",
                "mime": "image/svg+xml",
                "path": "conv:fi:turn_prev.user.attachments/diagram-scene-hub.svg",
                "base64": payload,
                "turn_id": "turn_prev",
                "meta": {
                    "physical_path": "turn_prev/attachments/diagram-scene-hub.svg",
                    "size_bytes": 7,
                },
            },
        ]
    }

    class _Settings:
        STORAGE_PATH = str(tmp_path)

    import kdcube_ai_app.apps.chat.sdk.config as cfg
    cfg.get_settings = lambda: _Settings()

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["conv:fi:turn_prev.user.attachments/diagram-scene-hub.svg"],
                }
            }
        },
        "outdir": str(outdir),
    }

    await handle_react_pull(ctx_browser=ctx, state=state, tool_call_id="pull_no_share")

    payload = _latest_payload(ctx)
    delivery = payload["user_delivery"]
    assert delivery.startswith("none")
    assert "share=true" in delivery
    assert "attachment_paths" in delivery


@pytest.mark.asyncio
async def test_pull_tool_spec_teaches_share_decision_rule():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.pull import TOOL_SPEC

    purpose = TOOL_SPEC["purpose"]
    assert "share=true" in purpose
    assert "read/analyze" in purpose or "reference material" in purpose
    assert "Files tab" in purpose
    assert "attachment_paths" in purpose
    share_doc = TOOL_SPEC["args"]["share"]
    assert "the user receives no file" in share_doc
