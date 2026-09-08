# SPDX-License-Identifier: MIT
"""The install plan behind maintainer-selected package sources."""
import importlib.util
import json
from pathlib import Path

DOCKER_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = DOCKER_ROOT / "scripts/kdcube_local_python_packages_plan.py"


def _load():
    spec = importlib.util.spec_from_file_location("plan_tool", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tmp_path: Path, distributions):
    stage = tmp_path / "local-python-packages"
    (stage / "sources").mkdir(parents=True)
    (stage / "manifest.json").write_text(json.dumps({"packages": [{"distribution": d, "source": f"/src/{d}"} for d in distributions]}))
    (stage / "requirements.txt").write_text("".join(f"/tmp/kdcube-local-python-packages/sources/{d}\n" for d in distributions))
    return stage


def test_selected_distributions_leave_the_ordinary_pass_and_keep_their_extras(tmp_path: Path):
    tool = _load()
    (tmp_path / "requirements-aws.txt").write_text("boto3>=1.34\n")
    req = tmp_path / "requirements.txt"
    req.write_text(
        "fastapi>=0.110\n"
        "-r requirements-aws.txt\n"
        "connection-hub>=2026.09.02.0000\n"
        "app-foundation[mcp]>=2026.09.03.1835,<2027\n"
        "# a comment\n"
        "App_Foundation[extra2] ; python_version >= '3.12'\n"
    )
    stage = _stage(tmp_path, ["app-foundation", "connection-hub"])
    out = tmp_path / "plan"
    dropped, local = tool.plan(req, stage, out)

    ordinary = (out / "requirements.txt").read_text()
    assert "fastapi>=0.110" in ordinary
    assert "boto3>=1.34" in ordinary, "nested -r files are inlined"
    assert "-r requirements-aws.txt" not in ordinary
    assert "\nconnection-hub>=" not in ordinary and "\napp-foundation[mcp]" not in ordinary
    assert dropped == 3
    assert local == [
        f"{stage}/sources/app-foundation[extra2,mcp]",
        f"{stage}/sources/connection-hub",
    ]
    assert (out / "local-install.txt").read_text().splitlines() == local


def test_no_selection_means_ordinary_requirements_only(tmp_path: Path):
    tool = _load()
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi>=0.110\nconnection-hub>=2026.09.02.0000\n")
    stage = tmp_path / "local-python-packages"
    stage.mkdir()
    (stage / ".gitkeep").write_text("")
    out = tmp_path / "plan"
    dropped, local = tool.plan(req, stage, out)
    assert dropped == 0 and local == []
    assert (out / "local-install.txt").read_text() == ""
    assert "connection-hub>=2026.09.02.0000" in (out / "requirements.txt").read_text()


def test_manifest_absent_falls_back_to_staged_paths(tmp_path: Path):
    tool = _load()
    stage = tmp_path / "local-python-packages"
    stage.mkdir()
    (stage / "requirements.txt").write_text("/tmp/kdcube-local-python-packages/sources/connection_hub\n")
    assert tool.selected_distributions(stage) == ["connection-hub"]
