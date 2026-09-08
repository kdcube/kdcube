# SPDX-License-Identifier: MIT
"""The maintainer-selected package hook in every Python image, and its cache
discipline: the ordinary requirements are keyed by the manifest alone, the
selected sources arrive in a late layer, and the browser download never sits
behind either of them."""
from pathlib import Path


DOCKER_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGE_DOCKERFILES = (
    "all_in_one_kdcube/Dockerfile_Chatproc",
    "all_in_one_kdcube/Dockerfile_Exec",
    "all_in_one_kdcube/Dockerfile_Ingress",
    "all_in_one_kdcube/Dockerfile_Metricservice",
    "all_in_one_kdcube/Dockerfile_PostgresSetup",
    "custom-ui-managed-infra/Dockerfile_Chatproc",
    "custom-ui-managed-infra/Dockerfile_Exec",
    "custom-ui-managed-infra/Dockerfile_Ingress",
    "custom-ui-managed-infra/Dockerfile_Metricservice",
    "custom-ui-managed-infra/Dockerfile_PostgresSetup",
)

PLAN_TOOL = "COPY deployment/docker/scripts/kdcube_local_python_packages_plan.py /usr/local/bin/kdcube-local-packages-plan"
MANIFEST_COPY = (
    "COPY deployment/docker/local-python-packages/.gitkeep \\\n"
    "     deployment/docker/local-python-packages/manifest.jso[n] \\\n"
    "     deployment/docker/local-python-packages/requirements.tx[t] \\\n"
    "     /tmp/kdcube-local-python-packages/"
)
PLAN_RUN = "RUN python /usr/local/bin/kdcube-local-packages-plan"
ORDINARY_INSTALL = "pip install -r /tmp/kdcube-pip/requirements.txt"
SOURCES_COPY = "COPY deployment/docker/local-python-packages/ /tmp/kdcube-local-python-packages/"
LOCAL_GUARD = "[ -s /tmp/kdcube-pip/local-install.txt ]"
LOCAL_INSTALL = "pip install -r /tmp/kdcube-pip/local-install.txt"
LOCAL_REAPPLY = "pip install --no-deps --force-reinstall -r /tmp/kdcube-pip/local-install.txt"
PIP_CACHE_MOUNT = "--mount=type=cache,target=/root/.cache/pip"


def test_python_images_install_staged_maintainer_package_overrides():
    for relative_path in LOCAL_PACKAGE_DOCKERFILES:
        dockerfile = (DOCKER_ROOT / relative_path).read_text(encoding="utf-8")

        for needle in (PLAN_TOOL, MANIFEST_COPY, PLAN_RUN, ORDINARY_INSTALL, SOURCES_COPY, LOCAL_GUARD, LOCAL_INSTALL, LOCAL_REAPPLY):
            assert dockerfile.count(needle) == 1, (relative_path, needle)

        # The old whole-directory copy ahead of the requirements is gone: it
        # keyed the requirements layer on the selected sources' content.
        assert "COPY deployment/docker/local-python-packages /tmp/kdcube-local-python-packages\n" not in dockerfile, relative_path
        # No pip layer runs without the shared cache mount, and no layer
        # throws the cache away.
        assert "--no-cache-dir -r" not in dockerfile, relative_path
        assert "PIP_NO_CACHE_DIR" not in dockerfile, relative_path
        assert dockerfile.count(PIP_CACHE_MOUNT) >= 2, relative_path

        manifest_index = dockerfile.index(MANIFEST_COPY)
        plan_index = dockerfile.index(PLAN_RUN)
        ordinary_index = dockerfile.index(ORDINARY_INSTALL)
        sources_index = dockerfile.index(SOURCES_COPY)
        local_index = dockerfile.index(LOCAL_INSTALL)
        reapply_index = dockerfile.index(LOCAL_REAPPLY)
        assert (
            manifest_index < plan_index < ordinary_index < sources_index < local_index < reapply_index
        ), relative_path


def test_browser_download_never_sits_behind_the_venv_or_the_sources():
    for relative_path in (
        "all_in_one_kdcube/Dockerfile_Chatproc",
        "custom-ui-managed-infra/Dockerfile_Chatproc",
    ):
        dockerfile = (DOCKER_ROOT / relative_path).read_text(encoding="utf-8")
        bootstrap = "playwright-bootstrap/bin/python -m playwright install --with-deps chromium"
        venv_copy = "COPY --from=builder /opt/venv /opt/venv"
        reconcile = "RUN python -m playwright install chromium"
        source_copy = "COPY --chown=appuser:appuser src/kdcube-ai-app/ ."
        assert dockerfile.count(bootstrap) == 1, relative_path
        assert dockerfile.count(venv_copy) == 1, relative_path
        assert dockerfile.count(reconcile) == 1, relative_path
        assert (
            dockerfile.index(bootstrap) < dockerfile.index(venv_copy) < dockerfile.index(reconcile) < dockerfile.index(source_copy)
        ), relative_path
        assert "RUN chown -R appuser:appuser /app" not in dockerfile, relative_path

    for relative_path in (
        "all_in_one_kdcube/Dockerfile_Exec",
        "custom-ui-managed-infra/Dockerfile_Exec",
    ):
        dockerfile = (DOCKER_ROOT / relative_path).read_text(encoding="utf-8")
        browser = "RUN python -m playwright install --with-deps chromium"
        assert dockerfile.count(browser) == 1, relative_path
        assert (
            dockerfile.index(ORDINARY_INSTALL) < dockerfile.index(browser) < dockerfile.index(SOURCES_COPY)
        ), relative_path


def test_platform_source_is_the_last_copy_in_every_image():
    for relative_path in LOCAL_PACKAGE_DOCKERFILES:
        dockerfile = (DOCKER_ROOT / relative_path).read_text(encoding="utf-8")
        source_copy = "src/kdcube-ai-app/ "
        assert dockerfile.index(SOURCES_COPY) < dockerfile.rindex(source_copy), relative_path
