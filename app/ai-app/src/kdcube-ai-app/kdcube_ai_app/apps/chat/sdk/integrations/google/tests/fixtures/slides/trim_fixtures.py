"""Build neutral, adapter-focused fixtures from the earlier complete fixtures.

Usage: python trim_fixtures.py --source-ref <complete-fixture-commit>
Only the standard library is used; this does not import or execute the SDK.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAMES = ("simple_table", "streamline_rotated", "focus_group", "material_group_rotated", "pptx_converted")
RETAINED_FIELDS = {
    "presentation": ["presentationId", "title", "revisionId", "pageSize", "slides", "layouts", "masters"],
    "page": ["objectId", "pageElements", "slideProperties", "layoutProperties", "notesProperties"],
    "slideProperties": ["isSkipped", "layoutObjectId", "masterObjectId", "notesPage"],
    "layoutProperties": ["name", "displayName", "masterObjectId"],
    "element": ["objectId", "title", "description", "size", "transform", "shape", "table", "elementGroup", "image", "sheetsChart", "video"],
    "shape": ["shapeType", "placeholder.type", "placeholder.parentObjectId", "text.textElements"],
    "textElement": ["startIndex", "endIndex", "textRun.content", "textRun.style.fontSize", "textRun.style.fontFamily", "textRun.style.bold", "paragraphMarker.style.lineSpacing", "paragraphMarker.bullet.glyph"],
    "table": ["rows", "columns", "tableRows[].tableCells[].text.textElements"],
    "notesProperties": ["speakerNotesObjectId"],
}
TAG = re.compile(r"<tag:([^>\n]+)>|\{\{([^{}\n]+)\}\}")
_tags = {}


def keep(value, keys):
    return {key: value[key] for key in keys if key in value}


def neutral_char(char):
    if char.isalpha():
        script = unicodedata.name(char, "").split()[0]
        upper, lower = {"LATIN": ("X", "x"), "CYRILLIC": ("Ж", "ж"), "GREEK": ("Φ", "φ")}[script]
        return lower if char.islower() else upper
    return "0" if char.isdecimal() else char


def neutral_text(text):
    chars = [neutral_char(c) for c in text]
    for match in TAG.finditer(text):
        name = match.group(1) or match.group(2)
        if name not in _tags:
            index = len(_tags)
            letters = []
            for _ in name:
                letters.append(chr(65 + index % 26))
                index //= 26
            alias = "".join(reversed(letters))
            _tags[name] = "".join(a.lower() if c.islower() else a if c.isalpha() else neutral_char(c) for a, c in zip(alias, name))
        # Preserve the escaped-tag transport syntax and use neutral names
        # with the same length. Complete tags in these captures are Latin.
        alias = _tags[name]
        chars[match.start():match.end()] = list("<tag:" + alias + ">" if match.group(1) is not None else "{{" + alias + "}}")
    result = "".join(chars)
    assert len(result) == len(text)
    assert len(result.encode("utf-16-le")) == len(text.encode("utf-16-le"))
    assert [(i, c) for i, c in enumerate(result) if c.isspace()] == [(i, c) for i, c in enumerate(text) if c.isspace()]
    assert [0x0400 <= ord(c) <= 0x052F for c in result] == [0x0400 <= ord(c) <= 0x052F for c in text]
    def script_profile(value):
        return [(unicodedata.name(c, "").split()[0], c.islower()) if c.isalpha() else None for c in value]
    assert script_profile(result) == script_profile(text)
    return result


def text(value):
    elements = value.get("textElements", [])
    # Transform across run boundaries so a split template tag stays a tag.
    joined = "".join(e.get("textRun", {}).get("content", "") for e in elements)
    neutral, offset, output = neutral_text(joined), 0, []
    for element in elements:
        row = keep(element, ("startIndex", "endIndex"))
        if "textRun" in element:
            run = element["textRun"]
            length = len(run.get("content", ""))
            row["textRun"] = {"content": neutral[offset:offset + length]}
            offset += length
            if "style" in run:
                row["textRun"]["style"] = keep(run["style"], ("fontSize", "fontFamily", "bold"))
        if "paragraphMarker" in element:
            marker = element["paragraphMarker"]
            row["paragraphMarker"] = {}
            if "style" in marker:
                row["paragraphMarker"]["style"] = keep(marker["style"], ("lineSpacing",))
            if marker.get("bullet"):
                row["paragraphMarker"]["bullet"] = keep(marker["bullet"], ("glyph",)) or {"glyph": "●"}
        output.append(row)
    return {"textElements": output} if "textElements" in value else {}


def element(value):
    row = keep(value, ("objectId", "size", "transform"))
    for field in ("title", "description"):
        if field in value:
            row[field] = neutral_text(value[field])
    if "shape" in value:
        shape = value["shape"]
        # Keep shapeType: a shape with metadata but no text still receives
        # fit diagnostics, whereas an empty shape mapping does not.
        row["shape"] = keep(shape, ("shapeType",))
        if "placeholder" in shape:
            row["shape"]["placeholder"] = keep(shape["placeholder"], ("type", "parentObjectId"))
        if "text" in shape:
            row["shape"]["text"] = text(shape["text"])
    if "table" in value:
        table = value["table"]
        row["table"] = keep(table, ("rows", "columns"))
        row["table"]["tableRows"] = [{"tableCells": [{"text": text(c["text"])} if "text" in c else {} for c in r.get("tableCells", [])]} for r in table.get("tableRows", [])]
    if "elementGroup" in value:
        row["elementGroup"] = {"children": [element(e) for e in value["elementGroup"].get("children", [])]}
    for kind in ("image", "sheetsChart", "video"):
        if kind in value:
            row[kind] = {}  # Type presence is all the snapshot consumes.
    return row


def page(value):
    row = keep(value, ("objectId",))
    if "pageElements" in value:
        row["pageElements"] = [element(e) for e in value["pageElements"]]
    if "layoutProperties" in value:
        row["layoutProperties"] = keep(value["layoutProperties"], RETAINED_FIELDS["layoutProperties"])
    if "slideProperties" in value:
        props = value["slideProperties"]
        row["slideProperties"] = keep(props, ("isSkipped", "layoutObjectId", "masterObjectId"))
        if "notesPage" in props:
            row["slideProperties"]["notesPage"] = page(props["notesPage"])
    if "notesProperties" in value:
        row["notesProperties"] = keep(value["notesProperties"], ("speakerNotesObjectId",))
    return row


def walk(elements):
    for row in elements:
        yield row
        yield from walk(row.get("elementGroup", {}).get("children", []))


def support_pages(value, roots):
    """Keep inherited styles and layout placeholder metadata exactly."""
    support = [*value.get("layouts", []), *value.get("masters", [])]
    indexed = {e.get("objectId"): e for p in support for e in walk(p.get("pageElements", []))}
    def parent(row):
        return row.get("shape", {}).get("placeholder", {}).get("parentObjectId")
    pending = [parent(e) for e in roots if parent(e)]
    needed = set()
    while pending:
        ref = pending.pop()
        if ref in needed or ref not in indexed:
            continue
        needed.add(ref)
        inherited = parent(indexed[ref])
        if inherited:
            pending.append(inherited)
    def project(rows, *, layout):
        output = []
        for row in rows:
            if "elementGroup" in row:
                children = project(row["elementGroup"].get("children", []), layout=layout)
                if children:
                    output.append({"objectId": row["objectId"], "elementGroup": {"children": children}})
            elif row.get("objectId") in needed:
                output.append({"objectId": row["objectId"], "shape": row["shape"]})
            elif layout and row.get("shape", {}).get("placeholder", {}).get("type"):
                # Layout listings expose every placeholder's type, even
                # when no slide inherits its styling.
                output.append({"objectId": row["objectId"], "shape": {"placeholder": {"type": row["shape"]["placeholder"]["type"]}}})
        return output
    for kind in ("layouts", "masters"):
        for p in value.get(kind, []):
            if "pageElements" in p:
                p["pageElements"] = project(p["pageElements"], layout=kind == "layouts")


def presentation(value, *, include_slides=True, roots=None):
    row = keep(value, ("presentationId", "title", "revisionId", "pageSize"))
    for kind in ("slides", "layouts", "masters"):
        if kind in value and (kind != "slides" or include_slides):
            row[kind] = [page(p) for p in value[kind]]
    if roots is None:
        roots = [e for p in row.get("slides", []) for e in walk(p.get("pageElements", []))]
    support_pages(row, roots)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", required=True)
    args = parser.parse_args()
    repository = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repository).as_posix()
    def read(name):
        return subprocess.check_output(["git", "show", f"{args.source_ref}:{prefix}/{name}"], cwd=repository)
    provenance = json.loads(read("provenance.json"))
    before_total, after_total = 0, 0
    for name in (*NAMES, "fit-calibration"):
        raw = read(name + ".json")
        value = json.loads(raw)
        if name == "fit-calibration":
            # Each fit test consumes one written BODY plus a shared style
            # chain. Other page elements do not contribute to its fit.
            cases = []
            for c in value["cases"]:
                body = next(e for e in c["page_after_write"]["pageElements"] if e.get("shape", {}).get("placeholder", {}).get("type") == "BODY")
                projected_page = keep(c["page_after_write"], ("objectId",))
                projected_page["pageElements"] = [element(body)]
                cases.append({**keep(c, ("case", "deck", "script", "repeats", "chars", "thumbnail", "observed")), "text": neutral_text(c["text"]), "page_after_write": projected_page})
            output = {
                "note": "Trimmed captured API pages and style chains with neutral text of equal lengths, scripts and line breaks. Observations and thumbnail hashes describe the original pre-neutralisation rendering, not a rendering of this neutral text.",
                "source_sha256": value["source_sha256"],
                "cases": cases,
                "copy_presentations_after": {k: presentation(v, include_slides=False, roots=[e for c in cases if c["deck"] == k for e in c["page_after_write"]["pageElements"]]) for k, v in value["copy_presentations_after"].items()},
            }
            record = provenance.setdefault("calibration", {"capture_sha256": value["source_sha256"]})
        else:
            output = presentation(value)
            record = provenance["fixtures"][name]
        encoded = (json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        (ROOT / (name + ".json")).write_bytes(encoded)
        record.update(fixture_sha256=hashlib.sha256(encoded).hexdigest(), before_bytes=len(raw), after_bytes=len(encoded))
        before_total += len(raw)
        after_total += len(encoded)
    provenance["transformations"] = [
        "Start from the complete captured-response fixtures at the supplied source ref; preserve original capture hashes.",
        "Retain only the listed adapter and calibration fields; omit unused page properties, theme data, shape properties, borders and provider URLs.",
        "Neutralise all text runs, notes, table cells and alt text, preserving code-point and UTF-16 lengths, scripts, whitespace and line breaks. Assign neutral tag names of equal length.",
        "Keep neutral presentation aliases and original layout names; preserve missing fields such as the PPTX revisionId.",
        "Calibration copies keep page size, identity and layout/master chains; each test supplies its captured case page instead of duplicate copy slides.",
        "Keep only parent-chain shapes on layouts and masters; retain placeholder types and layoutProperties for every layout to preserve the complete layout listing.",
        "Each calibration page keeps only the written BODY element; a shared parent chain per deck supplies inherited styles. Other page diagnostics are outside this calibration projection.",
        "Keep measured observations and original thumbnail hashes. Neutral strings preserve estimator inputs, not exact rendered glyph widths.",
        "Serialize fixture bodies as compact UTF-8 JSON; keep provenance indented for reading.",
    ]
    provenance["retained_fields"] = RETAINED_FIELDS
    provenance["complete_fixture_source_ref"] = args.source_ref
    provenance["sizes"] = {"before_bytes": before_total, "after_bytes": after_total}
    (ROOT / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(provenance["sizes"]))


if __name__ == "__main__":
    main()
