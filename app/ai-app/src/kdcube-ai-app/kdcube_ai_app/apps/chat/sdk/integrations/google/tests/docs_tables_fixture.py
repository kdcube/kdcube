# SPDX-License-Identifier: MIT
"""Builds Google Docs API document trees with consistent UTF-16 indices."""
from __future__ import annotations

from typing import Any


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class Body:
    """Appends paragraphs and tables the way documents.get reports them."""

    def __init__(self) -> None:
        self.content: list[dict[str, Any]] = []
        self.index = 1

    def _paragraph(
        self,
        text: str,
        *,
        style: str = "NORMAL_TEXT",
        objects: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        start = self.index
        elements: list[dict[str, Any]] = []
        cursor = start
        for entry in objects:
            spec = entry if isinstance(entry, dict) else {"kind": entry}
            kind = spec["kind"]
            element: dict[str, Any] = {"startIndex": cursor, "endIndex": cursor + 1}
            properties = {
                # Docs answers a date chip with displayText, not text.
                ("displayText" if key == "text" and kind == "dateElement" else key): value
                for key, value in spec.items()
                if key != "kind" and value
            }
            if kind == "inlineObjectElement":
                # An image element points into the document-level inlineObjects map.
                element[kind] = {"inlineObjectId": spec.get("id") or "img.1"}
            else:
                element[kind] = (
                    {f"{kind}Properties": properties} if properties else {}
                )
            elements.append(element)
            cursor += 1
        run = text + "\n"
        elements.append(
            {
                "startIndex": cursor,
                "endIndex": cursor + utf16_len(run),
                "textRun": {"content": run, "textStyle": {}},
            }
        )
        end = cursor + utf16_len(run)
        self.index = end
        return {
            "startIndex": start,
            "endIndex": end,
            "paragraph": {
                "elements": elements,
                "paragraphStyle": {"namedStyleType": style},
            },
        }

    def paragraph(
        self,
        text: str,
        *,
        style: str = "NORMAL_TEXT",
        objects: tuple[Any, ...] = (),
    ) -> "Body":
        self.content.append(self._paragraph(text, style=style, objects=objects))
        return self

    def heading(self, text: str, level: int = 2) -> "Body":
        return self.paragraph(text, style=f"HEADING_{level}")

    def table(
        self,
        rows: list[list[Any]],
        *,
        header_rows: int = 0,
        spans: dict[tuple[int, int], tuple[int, int]] | None = None,
        omit_covered: bool = False,
        column_widths: list[float | None] | None = None,
    ) -> "Body":
        """rows hold cell text, or dicts {text, objects, nested, paragraphs}.

        ``spans`` maps a 0-based head cell to (row_span, column_span); covered
        cells stay in the grid as empty cells unless ``omit_covered``.
        """
        spans = spans or {}
        covered = {
            (r + dr, c + dc)
            for (r, c), (rs, cs) in spans.items()
            for dr in range(rs)
            for dc in range(cs)
            if dr or dc
        }
        table_start = self.index
        self.index += 1
        table_rows: list[dict[str, Any]] = []
        for r, row in enumerate(rows):
            row_start = self.index
            self.index += 1
            cells: list[dict[str, Any]] = []
            for c, value in enumerate(row):
                if omit_covered and (r, c) in covered:
                    continue
                spec = value if isinstance(value, dict) else {"text": value}
                cell_start = self.index
                self.index += 1
                content: list[dict[str, Any]] = []
                paragraphs = spec.get("paragraphs") or [spec.get("text", "")]
                for number, text in enumerate(paragraphs):
                    content.append(
                        self._paragraph(
                            text,
                            objects=tuple(spec.get("objects", ())) if number == 0 else (),
                        )
                    )
                if spec.get("nested"):
                    nested = Body()
                    nested.index = self.index
                    nested.table([["inner"]])
                    content.append(nested.content[0])
                    self.index = nested.index
                    content.append(self._paragraph(""))
                style: dict[str, Any] = {}
                if (r, c) in spans:
                    style = {"rowSpan": spans[(r, c)][0], "columnSpan": spans[(r, c)][1]}
                cells.append(
                    {
                        "startIndex": cell_start,
                        "endIndex": self.index,
                        "content": content,
                        "tableCellStyle": style,
                    }
                )
            table_rows.append(
                {
                    "startIndex": row_start,
                    "endIndex": self.index,
                    "tableCells": cells,
                    "tableRowStyle": {"tableHeader": True} if r < header_rows else {},
                }
            )
        self.index += 1
        self.content.append(
            {
                "startIndex": table_start,
                "endIndex": self.index,
                "table": {
                    "rows": len(rows),
                    "columns": max(len(row) for row in rows),
                    "tableRows": table_rows,
                    **(
                        {
                            "tableStyle": {
                                "tableColumnProperties": [
                                    {
                                        "widthType": "FIXED_WIDTH",
                                        "width": {"magnitude": width, "unit": "PT"},
                                    }
                                    if width is not None
                                    else {"widthType": "EVENLY_DISTRIBUTED"}
                                    for width in column_widths
                                ]
                            }
                        }
                        if column_widths
                        else {}
                    ),
                },
            }
        )
        return self

    def as_body(self) -> dict[str, Any]:
        return {"content": self.content}


def inline_image(
    *,
    alt: str = "",
    title: str = "",
    width_pt: float | None = None,
    height_pt: float | None = None,
    source_uri: str = "",
    content_uri: str = "",
) -> dict[str, Any]:
    """One ``inlineObjects`` entry, as documents.get reports an inline image."""
    embedded: dict[str, Any] = {}
    if title:
        embedded["title"] = title
    if alt:
        embedded["description"] = alt
    if width_pt is not None or height_pt is not None:
        embedded["size"] = {
            "width": {"magnitude": width_pt, "unit": "PT"},
            "height": {"magnitude": height_pt, "unit": "PT"},
        }
    image: dict[str, Any] = {}
    if content_uri:
        image["contentUri"] = content_uri
    if source_uri:
        image["sourceUri"] = source_uri
    if image:
        embedded["imageProperties"] = image
    return {"inlineObjectProperties": {"embeddedObject": embedded}}


DEFAULT_PAGE_WIDTH_PT = 612.0
DEFAULT_MARGIN_PT = 72.0


def document(
    *tabs: tuple[str, str, Body],
    revision: str = "rev-1",
    document_id: str = "DOC1",
    inline_objects: dict[str, Any] | None = None,
    page_width_pt: float | None = DEFAULT_PAGE_WIDTH_PT,
    margin_pt: float = DEFAULT_MARGIN_PT,
) -> dict[str, Any]:
    """A document with one (tab_id, title, body) entry per tab.

    ``inline_objects`` is served on every tab, which is what a single-tab test needs.
    """
    return {
        "documentId": document_id,
        "title": "Tables",
        "revisionId": revision,
        "tabs": [
            {
                "tabProperties": {"tabId": tab_id, "title": title, "index": index},
                "documentTab": {
                    "body": body.as_body(),
                    **({"inlineObjects": dict(inline_objects)} if inline_objects else {}),
                    **(
                        {
                            "documentStyle": {
                                "pageSize": {
                                    "width": {"magnitude": page_width_pt, "unit": "PT"}
                                },
                                "marginLeft": {"magnitude": margin_pt, "unit": "PT"},
                                "marginRight": {"magnitude": margin_pt, "unit": "PT"},
                            }
                        }
                        if page_width_pt
                        else {}
                    ),
                },
            }
            for index, (tab_id, title, body) in enumerate(tabs)
        ],
    }


def tasks_body() -> Body:
    """H2 Tasks, a note, a header-marked task table, H3 Budget, a plain table."""
    return (
        Body()
        .paragraph("Report 🚀 July")
        .heading("Tasks", 2)
        .paragraph("Open items for the week.")
        .table(
            [
                ["Task", "Status", "Owner"],
                ["Fix login", "", "owner-a"],
                ["Ship docs", "Open", ""],
                ["Review", "Open", "owner-b"],
            ],
            header_rows=1,
        )
        .heading("Budget", 3)
        .table([["Office", "120"], ["Travel", "300"]])
    )
