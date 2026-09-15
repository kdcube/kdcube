"""An XLSX the host cannot open for lack of a library is not reported as broken."""

from __future__ import annotations

import builtins
import zipfile
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifact_analysis import (
    analyze_write_tool_output,
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _padded_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("padding.txt", "x" * 4096)
    return path


def test_missing_openpyxl_is_a_warning_not_a_broken_file(tmp_path: Path, monkeypatch) -> None:
    real_import = builtins.__import__

    def _no_openpyxl(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("No module named 'openpyxl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_openpyxl)
    stats = analyze_write_tool_output(
        file_path=str(_padded_zip(tmp_path / "data.xlsx")), mime=XLSX, output_dir=None,
    )

    assert "write_error" not in stats
    assert stats["write_warning"].startswith("xlsx_not_validated")


def test_an_unreadable_workbook_is_still_an_error(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    stats = analyze_write_tool_output(
        file_path=str(_padded_zip(tmp_path / "data.xlsx")), mime=XLSX, output_dir=None,
    )

    assert stats["write_error"].startswith("xlsx_open_failed")
