# SPDX-License-Identifier: MIT
"""Playwright's bundled Node driver must be able to start in every image that runs it.

Since Playwright 1.65 the driver bundles Node.js 26, whose Linux binaries link
against libatomic.so.1. The driver has to start before `playwright install
--with-deps` can install anything, and `--with-deps` covers the browser's OS
libraries, not the driver's own, so python:3.12-slim images fail with exit 127
unless the OS package layer provides libatomic1. Every Dockerfile that runs
Playwright is discovered here, so a new image cannot miss the prerequisite.
"""
import re
from pathlib import Path

DEPLOYMENT_ROOT = Path(__file__).resolve().parents[2]
PLAYWRIGHT_RUN = re.compile(r"playwright (install|install-deps)\b|pip install [^\n]*\bplaywright\b")
APT_INSTALL = re.compile(r"apt-get install(?:[^\n]*\\\n)*[^\n]*")  # the command with its continued lines


def _stages(text: str) -> list[str]:
    """The Dockerfile's build stages, each from its FROM line."""
    starts = [match.start() for match in re.finditer(r"(?m)^FROM\s", text)]
    return [text[start:end] for start, end in zip(starts, starts[1:] + [len(text)])]


def _playwright_dockerfiles() -> list[Path]:
    return sorted(path for path in DEPLOYMENT_ROOT.rglob("Dockerfile*")
                  if PLAYWRIGHT_RUN.search(path.read_text(encoding="utf-8")))


def test_every_image_that_runs_playwright_is_found():
    found = {str(path.relative_to(DEPLOYMENT_ROOT)) for path in _playwright_dockerfiles()}
    # The images known to run Playwright today; discovery may only grow this set.
    assert {
        "docker/all_in_one_kdcube/Dockerfile_Chatproc",
        "docker/all_in_one_kdcube/Dockerfile_Exec",
        "docker/custom-ui-managed-infra/Dockerfile_Chatproc",
        "docker/custom-ui-managed-infra/Dockerfile_Exec",
        "kubernetes/with-managed-infra/Dockerfile_Chatproc",
        "kubernetes/with-managed-infra/Dockerfile_Exec",
        "kubernetes/with-managed-infra/Dockerfile_Ingress",
    } <= found


def test_libatomic1_is_installed_before_playwright_in_the_same_stage():
    for path in _playwright_dockerfiles():
        for stage in _stages(path.read_text(encoding="utf-8")):
            first_playwright = PLAYWRIGHT_RUN.search(stage)
            if first_playwright is None:
                continue
            before = stage[:first_playwright.start()]
            installs = [block.group(0) for block in APT_INSTALL.finditer(before)]
            assert any(re.search(r"\blibatomic1\b", block) for block in installs), (
                f"{path.relative_to(DEPLOYMENT_ROOT)}: the stage runs Playwright without "
                "installing libatomic1 first in an apt-get install layer")
