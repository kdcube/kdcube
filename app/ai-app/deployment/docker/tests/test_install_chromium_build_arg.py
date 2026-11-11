# SPDX-License-Identifier: MIT
"""Chromium is an optional image layer.

Every Chromium download in the processor and executor images runs only when
the INSTALL_CHROMIUM build argument is "1" (its default), and compose passes
the value the CLI renders from assembly.yaml
platform.rendering.browser.local_chromium.
"""
import re
from pathlib import Path

DEPLOYMENT_ROOT = Path(__file__).resolve().parents[2]
GATED_DOCKERFILES = (
    "docker/all_in_one_kdcube/Dockerfile_Chatproc",
    "docker/all_in_one_kdcube/Dockerfile_Exec",
    "docker/custom-ui-managed-infra/Dockerfile_Chatproc",
    "docker/custom-ui-managed-infra/Dockerfile_Exec",
    "kubernetes/with-managed-infra/Dockerfile_Chatproc",
    "kubernetes/with-managed-infra/Dockerfile_Exec",
    "kubernetes/with-managed-infra/Dockerfile_Ingress",
)
GUARD = 'if [ "$INSTALL_CHROMIUM" = "1" ]; then'


def _run_instructions(dockerfile: str) -> list[str]:
    """Each RUN instruction with its backslash continuations joined."""
    joined = re.sub(r"\\\n", " ", dockerfile)
    return [line.strip() for line in joined.splitlines() if line.strip().startswith("RUN ")]


def test_every_chromium_download_is_behind_the_build_argument():
    for relative_path in GATED_DOCKERFILES:
        dockerfile = (DEPLOYMENT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "ARG INSTALL_CHROMIUM=1" in dockerfile, relative_path
        installs = [run for run in _run_instructions(dockerfile) if "playwright install" in run]
        assert installs, relative_path
        for run in installs:
            assert run.startswith(f"RUN {GUARD}"), (relative_path, run)
            assert run.rstrip().endswith("fi"), (relative_path, run)
        assert dockerfile.index("ARG INSTALL_CHROMIUM=1") < dockerfile.index(GUARD), relative_path


def test_the_chatproc_reconcile_step_is_gated_too():
    """The venv reconcile would otherwise download the browser the bootstrap skipped."""
    for relative_path in (
        "docker/all_in_one_kdcube/Dockerfile_Chatproc",
        "docker/custom-ui-managed-infra/Dockerfile_Chatproc",
    ):
        dockerfile = (DEPLOYMENT_ROOT / relative_path).read_text(encoding="utf-8")
        gated = [run for run in _run_instructions(dockerfile) if run.startswith(f"RUN {GUARD}")]
        assert len(gated) == 2, relative_path


def test_compose_passes_the_rendered_value_to_the_processor_build():
    for relative_path in (
        "docker/all_in_one_kdcube/docker-compose.yaml",
        "docker/custom-ui-managed-infra/docker-compose.yaml",
    ):
        compose = (DEPLOYMENT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "- INSTALL_CHROMIUM=${KDCUBE_INSTALL_CHROMIUM:-1}" in compose, relative_path
