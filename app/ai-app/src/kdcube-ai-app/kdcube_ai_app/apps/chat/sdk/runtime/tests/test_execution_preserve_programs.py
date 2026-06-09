import pathlib
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.execution import _preserve_executed_programs, _unwrap_tool_envelope
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.runtime import ReactSolverV2


def test_preserve_executed_programs_groups_files_under_execution_id(tmp_path):
    source_dir = tmp_path / "work"
    outdir = tmp_path / "out"
    source_dir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    (source_dir / "main.py").write_text("print('loader')\n", encoding="utf-8")
    (source_dir / "user_code.py").write_text("print('user')\n", encoding="utf-8")

    _preserve_executed_programs(
        source_dir=source_dir,
        outdir=outdir,
        logger=SimpleNamespace(log=lambda *_args, **_kwargs: None),
        execution_dir_name="exec_tc_example123",
    )

    exec_dir = outdir / "executed_programs" / "exec_tc_example123"
    assert exec_dir.is_dir()
    assert (exec_dir / "main.py").read_text(encoding="utf-8") == "print('loader')\n"
    assert (exec_dir / "user_code.py").read_text(encoding="utf-8") == "print('user')\n"


def test_next_tool_streamer_idx_counts_execution_directories_and_old_flat_files(tmp_path):
    outdir = tmp_path / "out"
    exec_dir = outdir / "executed_programs" / "exec_tc_a"
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "main.py").write_text("print('loader')\n", encoding="utf-8")

    idx = ReactSolverV2._next_tool_streamer_idx(SimpleNamespace(), outdir, "exec_tools.execute_code_python")
    assert idx == 1

    legacy_dir = tmp_path / "legacy_out"
    legacy_exec_dir = legacy_dir / "executed_programs"
    legacy_exec_dir.mkdir(parents=True, exist_ok=True)
    (legacy_exec_dir / "exec_tools.execute_code_python_0_main.py").write_text("print('old')\n", encoding="utf-8")
    (legacy_exec_dir / "exec_tools.execute_code_python_1_main.py").write_text("print('old')\n", encoding="utf-8")

    legacy_idx = ReactSolverV2._next_tool_streamer_idx(
        SimpleNamespace(), legacy_dir, "exec_tools.execute_code_python"
    )
    assert legacy_idx == 2


def test_unwrap_tool_envelope_keeps_plain_structured_ok_result():
    output = {
        "ok": True,
        "object_ref": "mem:mem_123",
        "memory": {"id": "mem_123", "text": "Remember this."},
    }

    unwrapped, error, ok = _unwrap_tool_envelope(output)

    assert ok is True
    assert error is None
    assert unwrapped is output


def test_unwrap_tool_envelope_unwraps_explicit_ret_wrapper():
    output = {
        "ok": True,
        "ret": {
            "object_ref": "mem:mem_123",
            "memory": {"id": "mem_123", "text": "Remember this."},
        },
    }

    unwrapped, error, ok = _unwrap_tool_envelope(output)

    assert ok is True
    assert error is None
    assert unwrapped == output["ret"]
