# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""PinSearchIndex tests — deterministic fake embedder + pure-python backend."""
from __future__ import annotations

import asyncio
import re
import tempfile
import time
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search import PinSearchIndex

VOCAB = ["rollout", "plan", "deploy", "alpha", "prod", "q3", "numbers",
         "beta", "gamma", "testing", "task", "note", "canvas", "report"]


async def fake_embed(texts):
    out = []
    for t in texts:
        toks = re.findall(r"[a-z0-9]+", str(t).lower())
        out.append([float(toks.count(w)) for w in VOCAB])
    return out


def _idx(tmp: Path) -> PinSearchIndex:
    return PinSearchIndex(db_path=tmp / "pins.sqlite", embed_fn=fake_embed, dim=len(VOCAB))


async def _run() -> None:
    now = time.time()
    b1 = [
        {"id": "p1", "label": "Rollout plan", "description": "deploy alpha to prod",
         "kind": "canvas", "logical_path": "cnv:u/b1/p1",
         "comments": [{"text": "needs q3 numbers"}], "updated_at": now},
        {"id": "p2", "label": "Beta notes", "description": "gamma testing",
         "kind": "note", "logical_path": "task:issue/42", "updated_at": now - 86400},
        {"id": "p4", "label": "trashed alpha", "kind": "note",
         "logical_path": "task:issue/trashed", "placement": "trashed", "updated_at": now},
    ]
    b2 = [{"id": "p3", "label": "alpha report", "kind": "task",
           "logical_path": "task:issue/99", "updated_at": now}]

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        idx = _idx(tmp)
        await idx.sync_board(b1, board_id="b1")
        await idx.sync_board(b2, board_id="b2")

        # search across all boards → the two "alpha" pins surface
        hits = await idx.search("alpha", top_k=10)
        ids = [h.metadata["card_id"] for h in hits]
        assert "p1" in ids and "p3" in ids, ids
        assert "p2" not in ids[:2], ids  # p2 has no 'alpha'
        assert "p4" not in ids, ids

        # board filter
        only_b1 = await idx.search("alpha", top_k=10, board_id="b1")
        assert [h.metadata["card_id"] for h in only_b1] == ["p1"], only_b1

        # namespace filter
        task_refs = await idx.search("alpha", top_k=10, namespaces=["task"])
        assert [h.metadata["card_id"] for h in task_refs] == ["p3"], task_refs

        # comment text is searchable
        q3 = await idx.search("q3 numbers", top_k=5)
        assert q3 and q3[0].metadata["card_id"] == "p1", q3

        # kind filter
        tasks = await idx.search("alpha report", top_k=10, kinds=["task"])
        assert [h.metadata["card_id"] for h in tasks] == ["p3"], tasks

        # metadata carries the native ref (for pin → open/attach)
        assert only_b1[0].metadata["ref"] == "cnv:u/b1/p1"

        # removal: re-sync b1 without p2 → p2 leaves the index
        await idx.sync_board([b1[0]], board_id="b1")
        all_after = await idx.search("beta gamma", top_k=10, board_id="b1")
        assert "p2" not in [h.metadata["card_id"] for h in all_after], all_after

    print("test_pin_index: ALL PASS")


def test_pin_index():
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
