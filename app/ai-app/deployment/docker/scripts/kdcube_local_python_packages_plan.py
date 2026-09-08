#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Plan the two-pass Python install behind maintainer-selected package sources.

The runtime images can carry unpublished source for distributions the platform
imports (``kdcube refresh --maintainer-local-python-package DIST=SOURCE_DIR``).
The CLI stages each selected source under ``deployment/docker/local-python-packages/
sources/<dist>`` next to a ``manifest.json`` naming the distributions.

Why a plan: the image must keep its ordinary requirements layer cached while a
maintainer edits a selected source, and it must still let a selected source
satisfy a version floor that no published release meets yet. Both hold when the
ordinary requirements are installed WITHOUT the selected distributions (keyed by
the manifest alone, which names distributions and nothing about their content)
and the selected sources are installed afterwards WITH the extras the ordinary
requirements asked for, so their own declared dependencies resolve against the
already-installed set.

Reads the image's requirements file (nested ``-r`` files are inlined, ``-c``
files are made absolute) and writes into ``--out``:

  requirements.txt   the ordinary requirements minus the selected distributions
  local-install.txt  one line per selected source, ``<path>[extras]``; empty
                     when nothing is selected

Standard library only: it runs inside every Python image before pip.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?")
_STAGE_SOURCES = "sources"


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def selected_distributions(stage: Path) -> list[str]:
    """Distribution names from manifest.json, else from the staged requirements
    paths (``.../sources/<dist>``), else nothing."""
    manifest = stage / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"unreadable manifest {manifest}: {exc}") from exc
        names = [
            canonical(str(entry.get("distribution") or ""))
            for entry in (data.get("packages") or [])
            if isinstance(entry, dict)
        ]
        return [name for name in names if name]
    staged = stage / "requirements.txt"
    if staged.is_file():
        names = []
        for line in staged.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = Path(line).parts
            if _STAGE_SOURCES in parts:
                names.append(canonical(parts[parts.index(_STAGE_SOURCES) + 1]))
        return names
    return []


def _inline(path: Path, seen: set[Path]) -> list[str]:
    """Requirement lines of ``path`` with nested ``-r`` files inlined and ``-c``
    paths made absolute, so the output is valid from any directory."""
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith(("-r ", "--requirement ")):
            nested = path.parent / stripped.split(None, 1)[1].strip()
            out.append(f"# inlined from {nested.name}")
            out.extend(_inline(nested, seen))
            continue
        if stripped.startswith(("-c ", "--constraint ")):
            nested = path.parent / stripped.split(None, 1)[1].strip()
            out.append(f"-c {nested.resolve()}")
            continue
        out.append(raw)
    return out


def plan(requirements: Path, stage: Path, out: Path) -> tuple[int, list[str]]:
    selected = selected_distributions(stage)
    wanted = set(selected)
    extras: dict[str, set[str]] = {name: set() for name in selected}

    kept: list[str] = []
    dropped = 0
    for line in _inline(requirements, set()):
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "-")):
            match = _NAME_RE.match(stripped)
            name = canonical(match.group(1)) if match else ""
            if name in wanted:
                dropped += 1
                if match and match.group(2):
                    extras[name].update(
                        e.strip() for e in match.group(2).strip("[]").split(",") if e.strip()
                    )
                kept.append(f"# selected source satisfies: {stripped}")
                continue
        kept.append(line)

    out.mkdir(parents=True, exist_ok=True)
    (out / "requirements.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")
    local_lines = []
    for name in selected:
        suffix = f"[{','.join(sorted(extras[name]))}]" if extras[name] else ""
        local_lines.append(f"{stage / _STAGE_SOURCES / name}{suffix}")
    (out / "local-install.txt").write_text(
        "".join(f"{line}\n" for line in local_lines), encoding="utf-8"
    )
    return dropped, local_lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--stage", required=True, type=Path, help="the staged local-python-packages directory")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    dropped, local_lines = plan(args.requirements, args.stage, args.out)
    if local_lines:
        print(f"maintainer-selected sources: {len(local_lines)} ({dropped} requirement line(s) deferred to them)")
        for line in local_lines:
            print(f"  {line}")
    else:
        print("no maintainer-selected sources; ordinary requirements only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
