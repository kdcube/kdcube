"""Prepare small neutral layout fixtures from captured API response files.

Only the standard library is used. Supply --capture-dir for the five
slides-raw-<theme>.json envelopes, --probe-dir for the two slides-p7-*.json
captures, and --measurements for the six inspected PNG measurement records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = runpy.run_path(str(ROOT / "trim_fixtures.py"))
NAMES = ("simple_table", "streamline_rotated", "focus_group", "material_group_rotated", "pptx_converted")


def context(raw, title):
    keep, page, walk = TOOLS["keep"], TOOLS["page"], TOOLS["walk"]
    result = keep(raw, ("presentationId", "revisionId", "pageSize"))
    result["title"] = title
    result["layouts"] = [page(p) for p in raw.get("layouts", []) if p.get("layoutProperties", {}).get("name") in ("TITLE_AND_BODY", "TITLE_AND_TWO_COLUMNS", "OBJECT", "TWO_OBJECTS")]
    result["masters"] = [page(p) for p in raw.get("masters", [])]
    roots = [e for p in result["layouts"] for e in walk(p.get("pageElements", []))]
    masters = {"masters": result["masters"]}
    TOOLS["support_pages"](masters, roots)
    if raw.get("slides"):
        first = raw["slides"][0]
        result["slides"] = [{"objectId": first["objectId"], "slideProperties": keep(first.get("slideProperties", {}), ("layoutObjectId", "masterObjectId", "isSkipped"))}]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path, required=True)
    parser.add_argument("--measurements", type=Path, required=True)
    args = parser.parse_args()
    target = ROOT / "s2a"
    target.mkdir(exist_ok=True)
    records = {}
    def read(path):
        data = path.read_bytes()
        records[path.name] = {"capture_sha256": hashlib.sha256(data).hexdigest(), "capture_bytes": len(data)}
        return json.loads(data)
    def write(name, value):
        data = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        (target / name).write_bytes(data)
        records.setdefault(name, {}).update(fixture_sha256=hashlib.sha256(data).hexdigest(), fixture_bytes=len(data))
    for name in NAMES:
        captured = read(args.capture_dir / ("slides-raw-" + name + ".json"))
        write(name + "-layouts.json", context(captured["presentation"], name + " layout fixture"))
    calibration = read(args.probe_dir / "slides-p7-pptx-object-calibration.json")
    observed = {r["case"]: r for r in read(args.measurements)}
    cases = []
    for case in calibration["cases"]:
        body = next(e for e in case["page_after_write"]["pageElements"] if e.get("shape", {}).get("placeholder", {}).get("type") == "BODY")
        row = TOOLS["keep"](case, ("case", "script", "repeats", "chars", "thumbnail"))
        row["text"] = TOOLS["neutral_text"](case["text"])
        row["page_after_write"] = {"objectId": case["page_after_write"]["objectId"], "pageElements": [TOOLS["element"](body)]}
        measure = observed[case["case"]]
        assert measure["thumbnail_sha256"] == case["thumbnail"]["sha256"]
        row["observed"] = {"visible_lines": measure["visible_lines"], "pitch_pt": measure["pitch_pt"], "overflow": measure["ink_beyond_frame"], "line_count_is_lower_bound": False}
        cases.append(row)
    copy_context = context(calibration["copy_presentation_after"], "PPTX OBJECT calibration fixture")
    copy_context.pop("slides", None)
    # A full original created-page projection is small and preserves the
    # exact notes placeholder and creation geometry used by integration tests.
    write("pptx-object-calibration.json", {"context": copy_context, "created_page": TOOLS["page"](calibration["created_page"]), "cases": cases})
    unmapped = read(args.probe_dir / "slides-p7-unmapped-body.json")
    created = TOOLS["page"](unmapped["created_page"])
    body = next(e for e in created["pageElements"] if e.get("shape", {}).get("placeholder", {}).get("type") == "BODY")
    write("unmapped-body.json", {"created_page": created, "layout": TOOLS["page"](unmapped["layout"]), "unmapped_body_id": body["objectId"]})
    provenance = {
        "capture_date": "2026-10-28", "provider": "Google Slides API v1", "files": records,
        "transformations": [
            "Retain captured layout names, placeholder IDs/parents, size/transform and effective text/paragraph styles for supported title/body and two-column layouts.",
            "Keep only referenced master style chains. The first existing slide keeps identity/master/layout metadata for insertion-master resolution; its content is omitted.",
            "Use neutral presentation titles. Neutralise captured run, notes, table and alt text with the same lengths, scripts, whitespace and line breaks, as in the parent fixture preparation tool.",
            "Keep only the written BODY for each of six calibration cases, with one shared style context. Retain the created-page projection for notes and creation geometry.",
            "PNG observations and hashes describe original rendered text. A neutral-gray ink mask (chroma spread below 40, RGB distance above 180) excludes the colored decorative corner; all six images were inspected.",
            "Neutral strings preserve estimator inputs, not rendered glyph widths. JSON is compact UTF-8; no PNG bytes or provider URLs are committed.",
        ],
    }
    (target / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"files": len(list(target.glob('*.json'))), "bytes": sum(p.stat().st_size for p in target.glob('*.json'))}))


if __name__ == "__main__":
    main()
