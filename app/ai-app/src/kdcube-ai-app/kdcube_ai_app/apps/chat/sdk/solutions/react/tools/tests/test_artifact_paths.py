# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    build_logical_artifact_path,
    build_physical_artifact_path,
    normalize_physical_path,
    physical_path_to_logical_path,
    split_logical_artifact_ref,
    split_logical_artifact_path,
    split_physical_artifact_ref,
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
    assert physical_path_to_logical_path("turn_prev/snapshots/wizard-state.yaml") == "fi:turn_prev.snapshots/wizard-state.yaml"
    assert (
        physical_path_to_logical_path("turn_2026-05-19-01-01-49-177/outputs/report.md")
        == "fi:turn_2026-05-19-01-01-49-177.outputs/report.md"
    )
    assert physical_path_to_logical_path("turn_prev.files/report.md") == "fi:turn_prev.files/report.md"
    assert physical_path_to_logical_path("turn_prev.outputs/report.md") == "fi:turn_prev.outputs/report.md"
    assert (
        physical_path_to_logical_path("turn_prev/external/followup/attachments/mabc123/brief.txt")
        == "fi:turn_prev.external.followup.attachments/mabc123/brief.txt"
    )


def test_cross_conversation_fi_paths_round_trip_with_conv_segment():
    logical = build_logical_artifact_path(
        turn_id="turn_prev",
        namespace="snapshots",
        relpath="wizard/current.yaml",
        conversation_id="conv_2",
    )
    physical = build_physical_artifact_path(
        turn_id="turn_prev",
        namespace="snapshots",
        relpath="wizard/current.yaml",
        conversation_id="conv_2",
    )

    assert logical == "fi:conv_conv_2.turn_prev.snapshots/wizard/current.yaml"
    assert physical == "conv_conv_2/turn_prev/snapshots/wizard/current.yaml"
    assert split_logical_artifact_ref(logical) == (
        "conv_2",
        "turn_prev",
        "snapshots",
        "wizard/current.yaml",
    )
    assert split_physical_artifact_ref(physical) == (
        "conv_2",
        "turn_prev",
        "snapshots",
        "wizard/current.yaml",
    )
    assert physical_path_to_logical_path(physical) == logical


def test_cross_conversation_fi_paths_round_trip_all_artifact_namespaces():
    cases = [
        ("files", "workspace/spec.md", "fi:conv_c2.turn_prev.files/workspace/spec.md", "conv_c2/turn_prev/files/workspace/spec.md"),
        ("outputs", "report.pdf", "fi:conv_c2.turn_prev.outputs/report.pdf", "conv_c2/turn_prev/outputs/report.pdf"),
        ("snapshots", "wizard/current.yaml", "fi:conv_c2.turn_prev.snapshots/wizard/current.yaml", "conv_c2/turn_prev/snapshots/wizard/current.yaml"),
        ("attachments", "evidence.png", "fi:conv_c2.turn_prev.user.attachments/evidence.png", "conv_c2/turn_prev/attachments/evidence.png"),
        (
            "attachments",
            "external/followup/attachments/msg_1/evidence.png",
            "fi:conv_c2.turn_prev.external.followup.attachments/msg_1/evidence.png",
            "conv_c2/turn_prev/external/followup/attachments/msg_1/evidence.png",
        ),
    ]

    for namespace, relpath, logical_expected, physical_expected in cases:
        logical = build_logical_artifact_path(
            turn_id="turn_prev",
            namespace=namespace,
            relpath=relpath,
            conversation_id="c2",
        )
        physical = build_physical_artifact_path(
            turn_id="turn_prev",
            namespace=namespace,
            relpath=relpath,
            conversation_id="c2",
        )

        assert logical == logical_expected
        assert physical == physical_expected
        assert split_logical_artifact_ref(logical) == ("c2", "turn_prev", namespace, relpath)
        assert split_physical_artifact_ref(physical) == ("c2", "turn_prev", namespace, relpath)
        assert physical_path_to_logical_path(physical) == logical


def test_normalize_physical_path_preserves_cross_conversation_scope():
    physical, rel, rewritten = normalize_physical_path(
        "fi:conv_c2.turn_prev.snapshots/wizard/current.yaml",
        turn_id="turn_current",
    )

    assert physical == "conv_c2/turn_prev/snapshots/wizard/current.yaml"
    assert rel == "wizard/current.yaml"
    assert rewritten is True


def test_logical_artifact_path_accepts_recoverable_separator_mixup():
    assert physical_path_to_logical_path("fi:turn_prev/outputs/report.md") == "fi:turn_prev.outputs/report.md"
    assert split_logical_artifact_path("fi:turn_prev/files/report.md") == (
        "turn_prev",
        "files",
        "report.md",
    )
    assert split_logical_artifact_path("fi:turn_prev/outputs/report.md") == (
        "turn_prev",
        "outputs",
        "report.md",
    )
    assert split_logical_artifact_path("fi:turn_prev/snapshots/wizard-state.yaml") == (
        "turn_prev",
        "snapshots",
        "wizard-state.yaml",
    )
    assert split_logical_artifact_path("fi:turn_prev/user.attachments/template.xlsx") == (
        "turn_prev",
        "attachments",
        "template.xlsx",
    )


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


def test_normalize_physical_path_rewrites_relative_snapshots_namespace_to_current_turn():
    physical, rel, rewritten = normalize_physical_path(
        "snapshots/wizard/current.yaml",
        turn_id="turn_cur",
    )

    assert physical == "turn_cur/snapshots/wizard/current.yaml"
    assert rel == "wizard/current.yaml"
    assert rewritten is True
