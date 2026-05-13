# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from kdcube_ai_app.storage.observed_file_locks import (
    ObservedFileLockTimeout,
    observed_file_lock,
)


def test_observed_file_lock_writes_and_clears_metadata(tmp_path):
    lock_path = tmp_path / "resource.lock"

    with observed_file_lock(
        lock_path=lock_path,
        resource_id="resource",
        operation="test.operation",
        instance_id="test-instance",
    ) as metadata:
        assert metadata["operation"] == "test.operation"
        assert metadata["instance_id"] == "test-instance"
        on_disk = json.loads(lock_path.read_text(encoding="utf-8"))
        assert on_disk["owner_token"] == metadata["owner_token"]
        assert on_disk["resource_id"] == "resource"

    assert lock_path.read_text(encoding="utf-8") == ""


def test_observed_file_lock_times_out_when_process_lock_is_held(tmp_path):
    lock_path = tmp_path / "resource.lock"

    with observed_file_lock(
        lock_path=lock_path,
        resource_id="resource",
        operation="outer",
    ):
        with pytest.raises(ObservedFileLockTimeout):
            with observed_file_lock(
                lock_path=lock_path,
                resource_id="resource",
                operation="inner",
                wait_seconds=0.01,
                poll_interval_seconds=0.001,
            ):
                raise AssertionError("inner lock should not be acquired")
