from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_evidence_module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("copilot_evidence_test", root / "evidence.py")
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_append_comm_records_and_payload_are_scoped_to_bundle(tmp_path):
    evidence = _load_evidence_module()
    record = {
        "record_id": "commrec_1",
        "recorded_at_ms": 1770000000000,
        "type": "kdcube.copilot.workflow.turn.completed",
        "socket_event": "chat_service",
        "event": {"step": "workflow", "status": "completed", "title": "Done"},
        "service": {"tenant": "t", "project": "p", "user": "u"},
        "conversation": {"conversation_id": "c", "turn_id": "turn"},
    }

    assert evidence.append_comm_records(storage_root=tmp_path, bundle_id="kdcube.copilot", records=[record]) == 1
    assert evidence.append_comm_records(storage_root=tmp_path, bundle_id="kdcube.copilot", records=[record]) == 0

    payload = evidence.build_widget_payload(storage_root=tmp_path, bundle_id="kdcube.copilot")

    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["events"][0]["type"] == "kdcube.copilot.workflow.turn.completed"
    assert payload["by_source"]["comm.record"] == 1
