# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.artifacts import (
    normalize_physical_path,
    physical_path_to_logical_path,
)


def test_normalize_physical_path_accepts_generic_fi_for_outdir_tools():
    physical, rel, rewritten = normalize_physical_path(
        "fi:logs/docker.err.log",
        turn_id="turn_cur",
        allow_generic_fi=True,
    )

    assert physical == "logs/docker.err.log"
    assert rel == "logs/docker.err.log"
    assert rewritten is False


def test_physical_path_to_logical_path_supports_generic_outdir_paths():
    assert physical_path_to_logical_path("logs/docker.err.log") == "fi:logs/docker.err.log"
    assert physical_path_to_logical_path("turn_prev/files/report.md") == "fi:turn_prev.files/report.md"
    assert physical_path_to_logical_path("turn_prev/outputs/report.md") == "fi:turn_prev.outputs/report.md"


def test_normalize_physical_path_rewrites_relative_files_namespace_to_current_turn():
    physical, rel, rewritten = normalize_physical_path(
        "files/demo_proj/README.md",
        turn_id="turn_cur",
    )

    assert physical == "turn_cur/files/demo_proj/README.md"
    assert rel == "demo_proj/README.md"
    assert rewritten is True


def test_normalize_physical_path_rewrites_relative_outputs_namespace_to_current_turn():
    physical, rel, rewritten = normalize_physical_path(
        "outputs/demo_proj/test_results.txt",
        turn_id="turn_cur",
    )

    assert physical == "turn_cur/outputs/demo_proj/test_results.txt"
    assert rel == "demo_proj/test_results.txt"
    assert rewritten is True
