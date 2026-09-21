# SPDX-License-Identifier: MIT
"""`kdcube bundle reload <id> --commit <ref>`: the host side of an activation that names what it loads.

The proc pins the ref in the mounted repository and loads a snapshot of that
commit (W209). This side pins the same ref in the host checkout first and
sends the sha as ``expected_commit``, so a branch that moves between this
look and the proc's is refused there, and reads the receipt back against the
pin. Nothing here writes the descriptor: an activation is a reload, and
durability across a proc restart is a separate act with its own verb.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional

_SHA = re.compile(r"^[0-9a-f]{40}$")


def host_path_for(container_path: str, *, host_root: Optional[Path], container_root: str) -> Optional[Path]:
    """The host directory behind a descriptor entry's container path, or None when no mapping applies."""

    cleaned = str(container_path or "").strip()
    if not cleaned or host_root is None:
        return None
    root = container_root.rstrip("/") or "/"
    if cleaned == root:
        return host_root
    if not cleaned.startswith(root + "/"):
        return None
    return host_root / cleaned[len(root) + 1 :]


def pin_ref(host_path: Path, ref: str) -> str:
    """Resolve ``ref`` to a full sha in the repository holding ``host_path``. Exits with the reason when it cannot."""

    clean = str(ref or "").strip()
    if not clean or clean.startswith("-"):
        raise SystemExit("--commit needs a commit, tag or branch name.")
    if not host_path.exists():
        raise SystemExit(f"Bundle path {host_path} does not exist on this host, so {clean} cannot be pinned here.")
    probe = subprocess.run(
        ["git", "-C", str(host_path), "rev-parse", "--verify", "--quiet", f"{clean}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or "").strip()
        raise SystemExit(
            f"{clean} does not name a commit in the repository holding {host_path}."
            + (f" git: {detail}" if detail else "")
        )
    return probe.stdout.strip()


def activation_payload(
    *,
    bundle_id: str,
    commit: Optional[str],
    expect: Optional[str],
    host_path: Optional[Path],
) -> tuple[dict[str, Any], list[str]]:
    """The reload payload for an activation, and the lines saying what was pinned where.

    ``commit`` goes as typed so the proc resolves it in its own view of the
    repository. ``expected_commit`` is ``expect`` when given, else the sha
    pinned in the host checkout. Without a host mapping nothing is pinned
    here and the proc's resolution is the only one, which the lines say.
    """

    payload: dict[str, Any] = {"bundle_id": bundle_id}
    lines: list[str] = []
    ref = str(commit or "").strip()
    expected = str(expect or "").strip()
    if expected and not ref:
        raise SystemExit("--expect fences a commit. Pass --commit as well.")
    if not ref:
        return payload, lines
    payload["commit"] = ref
    if expected:
        if not _SHA.match(expected):
            raise SystemExit("--expect takes a full 40-character commit sha.")
        payload["expected_commit"] = expected
        lines.append(f"Expected: {expected} (from --expect)")
        return payload, lines
    if host_path is None:
        lines.append(f"Commit: {ref}, not pinned on this host (no host mapping for the bundle path). The proc's resolution stands.")
        return payload, lines
    pinned = pin_ref(host_path, ref)
    payload["expected_commit"] = pinned
    lines.append(f"Expected: {pinned} ({ref} pinned in {host_path})")
    return payload, lines


def activation_lines(result: Mapping[str, Any], payload: Mapping[str, Any]) -> list[str]:
    """What the receipt says loaded, checked against what was asked for. Exits on a mismatch."""

    activation = result.get("activation")
    if not isinstance(activation, dict):
        if payload.get("commit"):
            raise SystemExit(
                "The proc accepted the reload but its receipt names no activation: "
                "that proc predates commit activation. Rebuild it before relying on --commit."
            )
        return ["Loaded: not named by this proc (receipt predates activation evidence)."]
    mode = str(activation.get("mode") or "")
    if mode == "snapshot":
        commit = str(activation.get("commit") or "")
        expected = str(payload.get("expected_commit") or "")
        if expected and commit != expected:
            raise SystemExit(
                f"Receipt names commit {commit[:12]} but {expected[:12]} was pinned: the fence did not hold. "
                "Treat this activation as unknown and reload with an explicit sha."
            )
        origin = str(activation.get("origin") or "")
        durable = bool(activation.get("durable"))
        lines = [
            f"Loaded: snapshot of {commit[:12]} ({activation.get('ref') or commit[:12]}, {origin or 'unknown origin'})",
            f"Snapshot: {activation.get('path')}",
        ]
        if not durable:
            lines.append(
                "Durable: no. The descriptor was not written, and a proc restart loads from its descriptor entry again."
            )
        else:
            lines.append("Durable: yes, from the descriptor entry's activation.commit.")
        return lines
    if mode == "local-path":
        head = str(activation.get("head") or "")
        if activation.get("error"):
            return [f"Loaded: mounted tree {activation.get('path')} (no git evidence: {activation.get('error')})"]
        state = "dirty" if activation.get("dirty") else "clean"
        changed = activation.get("changed_paths") or []
        line = f"Loaded: mounted tree at head {head[:12]}, {state}"
        if changed:
            line += f" ({len(changed)} changed path{'s' if len(changed) != 1 else ''} under the bundle)"
        return [line, "Named by evidence at the moment of the read, not fenced: pass --commit to fence."]
    if mode == "git":
        return [f"Loaded: git-backed at ref {activation.get('ref')} (commit {str(activation.get('git_commit') or '')[:12] or 'unknown'})"]
    return [f"Loaded: {mode or 'unknown'}"]
