# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kdcube_cli import deployment_provenance as provenance


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_platform_source_identity_names_clean_and_dirty_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    source = repo / "platform.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    _git(repo, "add", "platform.py")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    commit = _git(repo, "rev-parse", "HEAD")

    clean = provenance.platform_source_identity(repo)
    assert clean["commit"] == commit
    assert clean["dirty"] is False
    assert clean["version"] == f"git:{commit}"

    source.write_text("VERSION = 2\n", encoding="utf-8")
    dirty = provenance.platform_source_identity(repo)
    assert dirty["commit"] == commit
    assert dirty["dirty"] is True
    assert dirty["version"] == f"git:{commit}+dirty:{dirty['content_sha256']}"

    staged = tmp_path / "staged"
    staged.mkdir()
    provenance.write_platform_source_marker(staged, dirty)
    assert provenance.platform_source_identity(staged) == dirty


def test_selected_platform_source_identity_uses_release_install_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provenance,
        "platform_source_identity",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("git source must not be read")
        ),
    )

    selected = provenance.selected_platform_source_identity(
        tmp_path,
        install_metadata={
            "install_mode": "release",
            "platform_ref": "2026.09.23.9",
            "dockerhub_namespace": "example",
        },
        declared_platform_ref="2026.09.24.1",
    )

    assert selected == {
        "mode": "release",
        "repository": "example",
        "ref": "2026.09.24.1",
        "version": "release:example@2026.09.24.1",
    }


def test_image_receipt_is_keyed_by_image_id_and_retains_previous_build(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_one = {"mode": "git", "commit": "one", "version": "git:one"}
    source_two = {"mode": "git", "commit": "two", "version": "git:two"}
    image_ids = {"kdcube-chat-proc:latest": "sha256:image-one"}
    monkeypatch.setattr(
        provenance,
        "_compose_configuration",
        lambda **kwargs: {
            "services": {
                "chat-proc": {
                    "build": {"context": "."},
                    "image": "kdcube-chat-proc:latest",
                }
            }
        },
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_image_id",
        lambda reference, strict=True: image_ids[reference],
    )

    provenance.record_compose_image_receipts(
        workdir=tmp_path,
        docker_dir=tmp_path,
        env_file=tmp_path / ".env",
        source=source_one,
        services=["chat-proc"],
    )
    image_ids["kdcube-chat-proc:latest"] = "sha256:image-two"
    receipt = provenance.record_compose_image_receipts(
        workdir=tmp_path,
        docker_dir=tmp_path,
        env_file=tmp_path / ".env",
        source=source_two,
        services=["chat-proc"],
    )

    assert receipt["images"]["sha256:image-one"]["source"] == source_one
    assert receipt["images"]["sha256:image-two"]["source"] == source_two
    assert receipt["latest_services"]["chat-proc"] == {
        "service": "chat-proc",
        "image": "kdcube-chat-proc:latest",
        "image_id": "sha256:image-two",
        "source": source_two,
        "recorded_at": receipt["latest_services"]["chat-proc"]["recorded_at"],
    }
    persisted = json.loads(provenance.image_receipt_path(tmp_path).read_text(encoding="utf-8"))
    assert set(persisted["images"]) == {"sha256:image-one", "sha256:image-two"}


def test_running_only_receipt_excludes_inactive_profile_services(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provenance,
        "_compose_configuration",
        lambda **kwargs: {
            "services": {
                "chat-proc": {"build": ".", "image": "chat-proc:latest"},
                "proxylogin": {"build": ".", "image": "proxylogin:latest"},
            }
        },
    )
    monkeypatch.setattr(
        provenance,
        "_compose_processes",
        lambda **kwargs: [
            {"Service": "chat-proc", "State": "running"},
            {"Service": "proxylogin", "State": "exited"},
        ],
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_image_id",
        lambda reference, strict=True: {
            "chat-proc:latest": "sha256:proc"
        }[reference],
    )

    receipt = provenance.record_compose_image_receipts(
        workdir=tmp_path,
        docker_dir=tmp_path,
        env_file=tmp_path / ".env",
        source={"mode": "git", "version": "git:abc"},
        running_only=True,
    )

    assert set(receipt["latest_services"]) == {"chat-proc"}
    assert set(receipt["images"]) == {"sha256:proc"}


def test_invalid_image_receipt_fails_explicitly(tmp_path: Path) -> None:
    provenance._atomic_write_json(
        provenance.image_receipt_path(tmp_path),
        {
            "schema": provenance.RECEIPT_SCHEMA,
            "images": [],
            "latest_services": {},
        },
    )

    with pytest.raises(provenance.DeploymentProvenanceError, match="receipt is invalid"):
        provenance.load_image_receipts(tmp_path)


def test_running_service_attestation_reports_source_match_and_external_image(
    monkeypatch,
    tmp_path: Path,
) -> None:
    selected_source = {"mode": "git", "commit": "abc", "version": "git:abc"}
    receipt = {
        "schema": provenance.RECEIPT_SCHEMA,
        "images": {
            "sha256:proc": {
                "image_id": "sha256:proc",
                "source": selected_source,
            }
        },
        "latest_services": {
            "chat-proc": {
                "service": "chat-proc",
                "image": "kdcube-chat-proc:latest",
                "image_id": "sha256:proc",
                "source": selected_source,
            }
        },
    }
    provenance._atomic_write_json(provenance.image_receipt_path(tmp_path), receipt)
    monkeypatch.setattr(provenance, "platform_source_identity", lambda _path: selected_source)
    monkeypatch.setattr(
        provenance,
        "_compose_processes",
        lambda **kwargs: [
            {"ID": "container-proc", "Service": "chat-proc", "State": "running"},
            {"ID": "container-redis", "Service": "redis", "State": "running"},
        ],
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_containers",
        lambda identifiers: [
            {
                "Id": "container-proc",
                "Name": "/demo-chat-proc",
                "Image": "sha256:proc",
                "Config": {
                    "Image": "kdcube-chat-proc:latest",
                    "Labels": {"com.docker.compose.service": "chat-proc"},
                },
            },
            {
                "Id": "container-redis",
                "Name": "/demo-redis",
                "Image": "sha256:redis",
                "Config": {
                    "Image": "redis:7",
                    "Labels": {"com.docker.compose.service": "redis"},
                },
            },
        ],
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_image_id",
        lambda reference, strict=False: {
            "kdcube-chat-proc:latest": "sha256:proc",
            "redis:7": "sha256:redis",
        }[reference],
    )

    result = provenance.collect_running_service_attestation(
        workdir=tmp_path,
        docker_dir=tmp_path,
        env_file=tmp_path / ".env",
        repo_root=tmp_path,
    )

    assert result["status"] == "MATCH"
    by_service = {item["service"]: item for item in result["services"]}
    assert by_service["chat-proc"]["source"] == selected_source
    assert by_service["chat-proc"]["status"] == "MATCH"
    assert by_service["redis"]["source"] == {
        "mode": "image-reference",
        "reference": "redis:7",
        "image_id": "sha256:redis",
        "version": "sha256:redis",
    }
    assert by_service["redis"]["status"] == "MATCH"


def test_running_service_attestation_exposes_old_running_image_and_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    old_source = {"mode": "git", "commit": "old", "version": "git:old"}
    selected_source = {"mode": "git", "commit": "new", "version": "git:new"}
    provenance._atomic_write_json(
        provenance.image_receipt_path(tmp_path),
        {
            "schema": provenance.RECEIPT_SCHEMA,
            "images": {
                "sha256:old": {
                    "image_id": "sha256:old",
                    "source": old_source,
                }
            },
            "latest_services": {
                "chat-proc": {
                    "image_id": "sha256:new",
                    "source": selected_source,
                }
            },
        },
    )
    monkeypatch.setattr(provenance, "platform_source_identity", lambda _path: selected_source)
    monkeypatch.setattr(
        provenance,
        "_compose_processes",
        lambda **kwargs: [
            {"ID": "container-proc", "Service": "chat-proc", "State": "running"}
        ],
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_containers",
        lambda identifiers: [
            {
                "Id": "container-proc",
                "Name": "/demo-chat-proc",
                "Image": "sha256:old",
                "Config": {"Image": "kdcube-chat-proc:latest", "Labels": {}},
            }
        ],
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_image_id",
        lambda reference, strict=False: "sha256:new",
    )

    result = provenance.collect_running_service_attestation(
        workdir=tmp_path,
        docker_dir=tmp_path,
        env_file=tmp_path / ".env",
        repo_root=tmp_path,
    )

    service = result["services"][0]
    assert result["status"] == "MISMATCH"
    assert service["source"] == old_source
    assert service["status"] == "MISMATCH"
    assert {
        (item["expected_field"], item["expected"], item["actual_field"], item["actual"])
        for item in service["comparisons"]
        if item["status"] == "MISMATCH"
    } == {
        (
            "configured_image.image_id",
            "sha256:new",
            "running_container.image_id",
            "sha256:old",
        ),
        (
            "latest_build.image_id",
            "sha256:new",
            "running_container.image_id",
            "sha256:old",
        ),
        (
            "selected_source.version",
            "git:new",
            "deployed_source.version",
            "git:old",
        ),
    }


def test_platform_service_without_build_receipt_is_unknown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        provenance,
        "platform_source_identity",
        lambda _path: {"mode": "git", "commit": "abc", "version": "git:abc"},
    )
    monkeypatch.setattr(
        provenance,
        "_compose_processes",
        lambda **kwargs: [
            {"ID": "container-proc", "Service": "chat-proc", "State": "running"}
        ],
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_containers",
        lambda identifiers: [
            {
                "Id": "container-proc",
                "Name": "/demo-chat-proc",
                "Image": "sha256:proc",
                "Config": {"Image": "kdcube-chat-proc:latest", "Labels": {}},
            }
        ],
    )
    monkeypatch.setattr(
        provenance,
        "_inspect_image_id",
        lambda reference, strict=False: "sha256:proc",
    )

    result = provenance.collect_running_service_attestation(
        workdir=tmp_path,
        docker_dir=tmp_path,
        env_file=tmp_path / ".env",
        repo_root=tmp_path,
    )

    assert result["status"] == "UNKNOWN"
    assert result["services"][0]["status"] == "UNKNOWN"
    assert result["services"][0]["comparisons"][-1] == {
        "status": "UNKNOWN",
        "expected_field": "selected_source.version",
        "expected": "git:abc",
        "actual_field": "deployed_source.version",
        "actual": None,
    }
