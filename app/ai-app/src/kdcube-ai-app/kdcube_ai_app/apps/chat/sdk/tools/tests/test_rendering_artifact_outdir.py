# SPDX-License-Identifier: MIT

import asyncio
import base64
import zipfile

from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import (
    ARTIFACT_OUTPUT_ENV,
    RUNTIME_OUTPUT_ENV,
)
from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import (
    resolve_output_dir,
    resolve_runtime_output_dir,
)
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as io_tools
from kdcube_ai_app.apps.chat.sdk.tools.rendering_tools import RenderingTools


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_output_dir_resolves_to_artifact_root(tmp_path):
    runtime_outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    runtime_outdir.mkdir()
    workdir.mkdir()

    OUTDIR_CV.set(str(runtime_outdir))
    WORKDIR_CV.set(str(workdir))

    assert resolve_runtime_output_dir() == runtime_outdir.resolve()
    assert resolve_output_dir() == (runtime_outdir / "workdir").resolve()


def test_explicit_artifact_output_env_is_authoritative(tmp_path, monkeypatch):
    runtime_outdir = tmp_path / "runtime-out"
    artifact_outdir = tmp_path / "artifact-out"
    workdir = tmp_path / "work"
    runtime_outdir.mkdir()
    artifact_outdir.mkdir()
    workdir.mkdir()

    OUTDIR_CV.set(str(runtime_outdir))
    WORKDIR_CV.set(str(workdir))
    monkeypatch.setenv("EXEC_CONTAINER_ROLE", "executor")
    monkeypatch.setenv(ARTIFACT_OUTPUT_ENV, str(artifact_outdir))
    monkeypatch.setenv(RUNTIME_OUTPUT_ENV, str(runtime_outdir))

    assert resolve_output_dir() == artifact_outdir.resolve()
    assert resolve_runtime_output_dir() == runtime_outdir.resolve()


def test_io_tool_call_metadata_stays_in_runtime_root(tmp_path):
    runtime_outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    runtime_outdir.mkdir()
    workdir.mkdir()

    OUTDIR_CV.set(str(runtime_outdir))
    WORKDIR_CV.set(str(workdir))

    rel = asyncio.run(
        io_tools.save_tool_call(
            tool_id="demo.tool",
            description="demo",
            data={"ok": True},
        )
    )

    assert (runtime_outdir / rel).exists()
    assert not ((runtime_outdir / "workdir") / rel).exists()
    assert (runtime_outdir / "tool_calls_index.json").exists()


def test_write_pptx_embeds_image_from_artifact_root(tmp_path):
    runtime_outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    runtime_outdir.mkdir()
    workdir.mkdir()

    OUTDIR_CV.set(str(runtime_outdir))
    WORKDIR_CV.set(str(workdir))

    artifact_outdir = resolve_output_dir()
    image_rel = "turn_test/outputs/hektoria/retreat_trend.png"
    image_path = artifact_outdir / image_rel
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(_ONE_PIXEL_PNG)

    html = f"""
    <!DOCTYPE html>
    <html>
    <body>
      <section>
        <h1>Retreat Trend</h1>
        <img src="{image_rel}" style="width:3in; height:2in;">
      </section>
    </body>
    </html>
    """

    result = asyncio.run(
        RenderingTools().write_pptx(
            path="turn_test/outputs/hektoria/deck.pptx",
            content=html,
        )
    )

    assert result.get("ok") is True, result
    pptx_path = artifact_outdir / "turn_test/outputs/hektoria/deck.pptx"
    assert pptx_path.exists()
    assert not (runtime_outdir / "turn_test/outputs/hektoria/deck.pptx").exists()

    with zipfile.ZipFile(pptx_path) as zf:
        names = zf.namelist()

    assert any(name.startswith("ppt/media/") for name in names)
