import json
from pathlib import Path
from zipfile import ZipFile

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.evidence import (
    recorded_tool_items,
    xlsx_contains_tool_evidence,
)


def test_recorded_tool_items_returns_only_successful_list_results(
    tmp_path: Path,
) -> None:
    (tmp_path / "tool_calls_index.json").write_text(
        json.dumps({"web_tools.web_search": ["success.json", "failure.json"]}),
        encoding="utf-8",
    )
    (tmp_path / "success.json").write_text(
        json.dumps(
            {
                "ret": {
                    "ok": True,
                    "error": None,
                    "ret": [{"url": "https://example.test"}],
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "failure.json").write_text(
        json.dumps(
            {
                "ret": {
                    "ok": False,
                    "error": "offline",
                    "ret": [{"ignored": True}],
                }
            }
        ),
        encoding="utf-8",
    )

    assert recorded_tool_items(tmp_path, "web_tools.web_search") == [
        {"url": "https://example.test"}
    ]


def test_recorded_tool_items_ignores_missing_and_unsafe_entries(tmp_path: Path) -> None:
    (tmp_path / "tool_calls_index.json").write_text(
        json.dumps({"web_tools.web_search": ["missing.json", "../outside.json"]}),
        encoding="utf-8",
    )

    assert recorded_tool_items(tmp_path, "web_tools.web_search") == []


def test_xlsx_contains_title_and_url_from_same_tool_row(tmp_path: Path) -> None:
    workbook = tmp_path / "report.xlsx"
    with ZipFile(workbook, "w") as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet><text>Status of Python versions</text>"
            "<text>https://devguide.python.org/versions/</text></worksheet>",
        )

    rows = [
        {
            "title": "Status of Python versions",
            "url": "https://devguide.python.org/versions/",
        }
    ]
    assert xlsx_contains_tool_evidence(workbook, rows) is True
    assert (
        xlsx_contains_tool_evidence(
            workbook,
            [{"title": "Different result", "url": rows[0]["url"]}],
        )
        is False
    )


def test_xlsx_tool_evidence_rejects_invalid_inputs(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.xlsx"
    invalid.write_text("not an OOXML archive", encoding="utf-8")

    assert xlsx_contains_tool_evidence(invalid, []) is False
    assert (
        xlsx_contains_tool_evidence(
            invalid,
            [{"title": "Python", "url": "https://python.org/"}],
        )
        is False
    )
