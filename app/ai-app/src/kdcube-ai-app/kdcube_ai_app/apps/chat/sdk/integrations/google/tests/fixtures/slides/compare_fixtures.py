"""Compare snapshot diagnostics against the complete captured fixtures.

Run with the qualified SDK test interpreter and source overlay:
python compare_fixtures.py --source-ref <complete-fixture-commit>
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import unicodedata
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot

ROOT = Path(__file__).resolve().parent
NAMES = ("simple_table", "streamline_rotated", "focus_group", "material_group_rotated", "pptx_converted")
TEXT_FIELDS = {"text", "title", "notes", "alt_text"}


def text_profile(value):
    # Compare character positions, scripts, code-unit lengths and whitespace;
    # the rest of the snapshot (including fit values) compares exactly.
    return [
        ("space", char) if char.isspace() else
        ("letter", unicodedata.name(char, "").split()[0], char.islower(), len(char.encode("utf-16-le"))) if char.isalpha() else
        ("digit", len(char.encode("utf-16-le"))) if char.isdecimal() else
        ("literal", char)
        for char in value
    ]


def normalise(value):
    if isinstance(value, list):
        return [normalise(v) for v in value]
    if isinstance(value, dict):
        return {k: text_profile(v) if k in TEXT_FIELDS and isinstance(v, str) else normalise(v) for k, v in value.items()}
    return value


def compare(label, before, after):
    left, right = normalise(snapshot(before, limit=50)), normalise(snapshot(after, limit=50))
    assert left == right, f"Snapshot diagnostics or text profile changed: {label}"
    digest = hashlib.sha256(json.dumps(right, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"case": label, "projection_sha256": digest}


def compare_body(label, before, after, body_ref):
    def body(value):
        return next(e for e in snapshot(value, limit=50)["slides"][0]["elements"] if e["ref"] == body_ref)
    left, right = normalise(body(before)), normalise(body(after))
    assert left == right, f"Body diagnostic or text profile changed: {label}"
    digest = hashlib.sha256(json.dumps(right, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"case": label, "body_projection_sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", required=True)
    args = parser.parse_args()
    repository = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    prefix = ROOT.relative_to(repository).as_posix()
    def read(name):
        return json.loads(subprocess.check_output(["git", "show", f"{args.source_ref}:{prefix}/{name}.json"], cwd=repository))
    output = [compare(name, read(name), json.loads((ROOT / (name + ".json")).read_text())) for name in NAMES]
    before, after = read("fit-calibration"), json.loads((ROOT / "fit-calibration.json").read_text())
    assert len(before["cases"]) == len(after["cases"]) == 24
    for old, new in zip(before["cases"], after["cases"]):
        assert old["case"] == new["case"]
        assert old["observed"] == new["observed"] and old["thumbnail"] == new["thumbnail"]
        assert text_profile(old["text"]) == text_profile(new["text"])
        left = copy.deepcopy(before["copy_presentations_after"][old["deck"]])
        right = copy.deepcopy(after["copy_presentations_after"][new["deck"]])
        left["slides"], right["slides"] = [old["page_after_write"]], [new["page_after_write"]]
        body_ref = new["page_after_write"]["pageElements"][0]["objectId"]
        output.append(compare_body(old["case"], left, right, body_ref))
    print(json.dumps({"comparisons": len(output), "unchanged": True, "projections": output}, indent=2))


if __name__ == "__main__":
    main()
