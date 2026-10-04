# SPDX-License-Identifier: MIT
"""One durable, workdir-owned lifecycle for exact local deployment images.

Docker and the filesystem owner are trusted operator boundaries. Qualification
is an explicit local declaration of independently collected evidence, not an
authentication protocol. Resolved Compose output is transient and never logged
or persisted; the manifest contains image identities and configuration hashes.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from kdcube_cli.compose_profiles import compose_profile_args
from kdcube_cli.control.execution import CommandRunner, SubprocessCommandRunner

SCHEMA = "kdcube.deployment-preservation.v1"
HOLD_REPOSITORY_PREFIX = "kdcube-preservation/"
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")
MANIFEST_ID = re.compile(r"[a-f0-9]{64}\Z")
IMMUTABLE_FIELDS = (
    "schema", "manifest_id", "identity", "owner", "profiles", "configuration_sha256",
    "services", "auxiliary", "excluded_profiles", "source_receipts_sha256",
)


class DeploymentPreservationError(RuntimeError):
    """A named refusal before disruption, or an operation retaining its holds."""


def _fail(code: str, detail: str) -> None:
    raise DeploymentPreservationError(f"{code}: {detail}")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class DeploymentPreservation:
    def __init__(
        self, *, workdir: Path, docker_dir: Path, env_file: Path,
        profiles: Sequence[str] | None = None, runner: CommandRunner | None = None,
    ) -> None:
        self.workdir = Path(workdir).expanduser().resolve()
        self.docker_dir = Path(docker_dir).expanduser().resolve()
        self.env_file = Path(env_file).expanduser().resolve()
        self.root = self.workdir / ".kdcube" / "preservation"
        self.runner = runner or SubprocessCommandRunner()
        self.assembly = self._assembly()
        if profiles is None:
            args = compose_profile_args(self.assembly)
            profiles = args[1::2]
        self.profiles = tuple(sorted(set(profiles)))

    def _assembly(self) -> dict[str, Any]:
        path = self.workdir / "config" / "assembly.yaml"
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            _fail("configuration_invalid", "The staged assembly cannot be read.")
        if not isinstance(value, dict):
            _fail("configuration_invalid", "The staged assembly must be a mapping.")
        return value

    def manifest_path(self, manifest_id: str) -> Path:
        if not MANIFEST_ID.fullmatch(manifest_id):
            _fail("manifest_invalid", "Expected the complete native manifest ID.")
        return self.root / f"{manifest_id}.json"

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / "lifecycle.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _run(self, command: Sequence[str], *, missing_ok: bool = False) -> str | None:
        environment = os.environ.copy()
        environment["COMPOSE_ENV_FILES"] = str(self.env_file)
        try:
            result = self.runner.run(command, cwd=self.docker_dir, env=environment,
                                     capture_output=True, timeout=120)
        except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            _fail("command_failed", f"Docker execution failed ({type(exc).__name__}); holds are retained.")
        if result.returncode:
            if missing_ok:
                return None
            # Docker config/inspect output and stderr can include credentials.
            _fail("command_failed", f"Docker exited {result.returncode}; holds are retained.")
        return result.stdout

    def _compose(self, *, all_profiles: bool = False, override: Path | None = None) -> list[str]:
        command = ["docker", "compose", "--env-file", str(self.env_file),
                   "-f", str(self.docker_dir / "docker-compose.yaml")]
        if override is not None:
            command.extend(["-f", str(override)])
        for profile in (("*",) if all_profiles else self.profiles):
            command.extend(["--profile", profile])
        return command

    def _configuration(self) -> dict[str, Any]:
        self.assembly = self._assembly()
        raw = self._run([*self._compose(all_profiles=True), "config", "--format", "json"])
        try:
            value = json.loads(raw or "")
        except ValueError:
            _fail("configuration_invalid", "Compose did not return valid JSON.")
        if not isinstance(value, dict) or not isinstance(value.get("services"), dict) or not value["services"]:
            _fail("configuration_invalid", "Compose must name the complete service catalog.")
        if not isinstance(value.get("name"), str) or not value["name"]:
            _fail("configuration_invalid", "Compose must name the deployment project.")
        return value

    def _identity(self, config: Mapping[str, Any]) -> dict[str, str]:
        daemon = (self._run(["docker", "info", "--format", "{{.ID}}"]) or "").strip()
        if not daemon:
            _fail("identity_invalid", "Docker must report its daemon identity.")
        context = self.assembly.get("context") or {}
        return {"workdir": str(self.workdir), "docker_dir": str(self.docker_dir),
                "env_file": str(self.env_file), "compose_project": str(config["name"]),
                "daemon_id": daemon, "tenant": str(context.get("tenant") or ""),
                "project": str(context.get("project") or "")}

    def _configuration_hash(self, config: Mapping[str, Any]) -> str:
        normalized = copy.deepcopy(config)
        for details in normalized["services"].values():
            details.pop("image", None)
            details.pop("build", None)
        # Include descriptor contents as hashes, never their values. Secrets and
        # the resolved environment remain part of the compatibility fingerprint.
        descriptors = {}
        for path in sorted((self.workdir / "config").glob("*.yaml")):
            descriptors[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return _digest({"compose": normalized, "descriptors": descriptors, "profiles": self.profiles})

    def _catalog(self, config):
        selected, excluded = {}, {}
        for name, details in sorted(config["services"].items()):
            if not isinstance(details, dict):
                _fail("configuration_invalid", "Every service must be a mapping.")
            profiles = details.get("profiles") or []
            if profiles and not set(profiles).intersection(self.profiles):
                excluded[name] = sorted(profiles)
            else:
                reference = details.get("image")
                if not isinstance(reference, str) or not reference or reference.startswith("-"):
                    _fail("image_reference_missing", f"Service {name} needs an explicit image reference.")
                selected[name] = reference
        if not selected:
            _fail("configuration_invalid", "The selected deployment has no services.")
        return selected, excluded

    def _executor(self, config) -> str | None:
        services = (self.assembly.get("platform") or {}).get("services") or {}
        descriptor = ((services.get("proc") or {}).get("exec") or {}).get("py_code_exec_image")
        if descriptor:
            return str(descriptor)
        details = config["services"].get("chat-proc")
        if details is None:
            return None
        environment = details.get("environment") or {}
        if not isinstance(environment, dict):
            _fail("configuration_invalid", "Resolved Compose environment must be a mapping.")
        return str(environment.get("PY_CODE_EXEC_IMAGE") or "py-code-exec:latest")

    def _auxiliary_references(self, config) -> dict[str, str]:
        default = self._executor(config)
        references = {"py-code-exec": default} if default else {}
        path = self.workdir / "config" / "bundles.yaml"
        if not path.exists():
            return references
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            items = (value.get("bundles") or {}).get("items") or []
            if not isinstance(items, list):
                raise ValueError()
            for item in items:
                bundle_id = item.get("id")
                if not isinstance(bundle_id, str) or not bundle_id:
                    raise ValueError()
                cfg = item.get("config") or {}
                runtime = (cfg.get("execution") or {}).get("runtime", cfg.get("exec_runtime", {}))
                if isinstance(runtime, str):
                    runtime = {"mode": runtime}
                if not isinstance(runtime, dict):
                    raise ValueError()
                profiles = runtime.get("profiles") or {}
                if not isinstance(profiles, dict):
                    raise ValueError()
                # SDK resolution merges the base over each selected profile,
                # then picks image > docker_image > PY_CODE_EXEC_IMAGE. Bind
                # every declared local profile, including non-default ones.
                base = {k: v for k, v in runtime.items() if k != "profiles"}
                selected = next((runtime[k] for k in ("profile", "selected_profile", "default_profile", "use")
                                 if isinstance(runtime.get(k), str) and runtime[k].strip()), None)
                if selected and selected not in profiles:
                    _fail("executor_profile_unknown", "The selected executor profile is not declared.")
                variants = {"default": {**(profiles.get(selected) or {}), **base}}
                for name, profile in profiles.items():
                    if not isinstance(profile, dict) or not isinstance(name, str):
                        raise ValueError()
                    variants[f"profile/{name}"] = {**profile, **base}
                for name, variant in variants.items():
                    mode = str(variant.get("mode") or "docker").lower()
                    if mode not in {"none", "local", "docker", "fargate", "external"}:
                        _fail("executor_runtime_unsupported", "The executor routing mode is not supported.")
                    if mode in {"fargate", "external"}:
                        _fail("executor_runtime_unsupported", "External executor profiles cannot be restored by a local image lifecycle.")
                    reference = next((str(variant[k]).strip() for k in ("image", "docker_image", "PY_CODE_EXEC_IMAGE")
                                      if variant.get(k)), default)
                    if mode == "docker" and reference:
                        references[f"bundle/{bundle_id}/{name}"] = reference
        except (OSError, yaml.YAMLError, ValueError, AttributeError, TypeError):
            _fail("executor_configuration_invalid", "Bundle executor profiles are not a supported descriptor mapping.")
        return references

    def _image(self, reference: str, *, missing_ok: bool = False) -> str | None:
        if not reference or reference.startswith("-"):
            _fail("image_reference_invalid", "Expected a local image reference.")
        value = self._run(["docker", "image", "inspect", "--format", "{{.Id}}", reference], missing_ok=True)
        value = (value or "").strip()
        if not value:
            if missing_ok:
                return None
            _fail("image_missing", "A required exact baseline image is absent locally.")
        if not IMAGE_ID.fullmatch(value):
            _fail("image_identity_invalid", "Docker must report a complete immutable image ID.")
        return value

    def _containers(self, *, host: bool = False) -> list[dict[str, Any]]:
        command = ["docker", "ps", "-a", "-q"] if host else [*self._compose(all_profiles=True), "ps", "-a", "-q"]
        ids = (self._run(command) or "").split()
        containers = []
        for container_id in ids:
            try:
                value = json.loads(self._run(["docker", "inspect", container_id]) or "")
                if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
                    raise ValueError()
            except ValueError:
                _fail("container_evidence_invalid", "Docker inspect did not return one container.")
            containers.append(value[0])
        return containers

    def _belongs(self, container, identity) -> bool:
        labels = (container.get("Config") or {}).get("Labels") or {}
        return (labels.get("com.docker.compose.project") == identity["compose_project"] and
                labels.get("com.docker.compose.project.working_dir") == identity["docker_dir"])

    def _receipt(self) -> dict[str, Any]:
        path = self.workdir / ".kdcube" / "deployment-images.v1.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
        except (OSError, ValueError):
            _fail("receipt_invalid", "Existing deployment image evidence is unreadable.")
        return value

    def _capture(self, references, *, identity, containers, receipt):
        observed = {}
        for container in containers:
            if not self._belongs(container, identity):
                _fail("foreign_deployment", "A same-project container belongs to another Compose directory.")
            labels = (container.get("Config") or {}).get("Labels") or {}
            service = labels.get("com.docker.compose.service")
            if service in references:
                observed.setdefault(service, set()).add(container.get("Image"))
        result = {}
        for name, reference in references.items():
            ids = observed.get(name, set())
            if len(ids) > 1:
                _fail("baseline_ambiguous", f"Service {name} has different replica image IDs.")
            prior = (receipt.get("latest_services") or {}).get(name) or {}
            if ids:
                value, selection = next(iter(ids)), "container"
            elif prior.get("image_id"):
                if prior.get("image") != reference:
                    _fail("baseline_mismatch", f"Service {name} has incompatible existing image evidence.")
                value, selection = prior["image_id"], "receipt"
            else:
                value, selection = self._image(reference), "configured-local-image"
            if not isinstance(value, str) or not IMAGE_ID.fullmatch(value) or self._image(value) != value:
                _fail("image_missing", f"Service {name} has no intact local baseline ID.")
            result[name] = {"reference": reference, "image_id": value, "selection": selection}
        return result

    def _load(self, manifest_id) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_path(manifest_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _fail("manifest_missing", "The native manifest cannot be read in this workdir.")
        if (not isinstance(value, dict) or value.get("schema") != SCHEMA or
                value.get("manifest_id") != manifest_id or
                value.get("binding_sha256") != _digest({k: value.get(k) for k in IMMUTABLE_FIELDS})):
            _fail("manifest_invalid", "Manifest identity, completeness or immutable binding changed.")
        if value.get("state") not in {"preparing", "protected", "restoring", "restored", "releasing", "released"}:
            _fail("manifest_invalid", "Manifest state is invalid.")
        identity = value.get("identity") or {}
        if identity.get("workdir") != str(self.workdir) or identity.get("docker_dir") != str(self.docker_dir):
            _fail("foreign_manifest", "Manifest belongs to another deployment directory.")
        return value

    def _save(self, manifest):
        _atomic_json(self.manifest_path(manifest["manifest_id"]), manifest)

    @staticmethod
    def _rows(manifest):
        return [*manifest["services"].values(), *manifest["auxiliary"].values()]

    def _validate(self, manifest, *, holds: bool):
        config = self._configuration()
        identity = self._identity(config)
        references, excluded = self._catalog(config)
        auxiliary = self._auxiliary_references(config)
        if (manifest["identity"] != identity or manifest["configuration_sha256"] != self._configuration_hash(config) or
                {k: v["reference"] for k, v in manifest["services"].items()} != references or
                manifest["excluded_profiles"] != excluded or manifest["profiles"] != list(self.profiles) or
                {k: v["reference"] for k, v in manifest["auxiliary"].items()} != auxiliary):
            _fail("configuration_mismatch", "Deployment identity, complete map or configuration is incompatible.")
        self._validate_images(manifest, holds=holds)
        return config

    def _validate_images(self, manifest, *, holds: bool):
        namespace = f"{HOLD_REPOSITORY_PREFIX}{_digest(manifest['identity'])[:24]}:{manifest['manifest_id'][:24]}-"
        for name, row in [*manifest["services"].items(), *manifest["auxiliary"].items()]:
            expected = namespace + _digest(name)[:24]
            if row.get("hold_ref") != expected or self._image(row["image_id"]) != row["image_id"]:
                _fail("manifest_invalid", "An exact image or owned retention reference is invalid.")
            if holds and self._image(expected) != row["image_id"]:
                _fail("hold_mismatch", "A retention reference is missing or points at another image.")

    def prepare(self, *, key: str, owner: str, consumers: Sequence[str] = (), protect: bool = True):
        if not key.strip() or not owner.strip() or any(not c.strip() for c in consumers):
            _fail("preparation_invalid", "A stable operation key and named owner/consumers are required.")
        with self._locked():
            manifest_id = _digest({"workdir": str(self.workdir), "key": key})
            if self.manifest_path(manifest_id).exists():
                manifest = self._load(manifest_id)
                if (manifest["owner"] != owner or set(manifest["consumers"]) != set(consumers) or
                        manifest["state"] in {"releasing", "released"}):
                    _fail("preparation_conflict", "Operation key already names a different or released lifecycle.")
            else:
                config = self._configuration()
                identity = self._identity(config)
                references, excluded = self._catalog(config)
                containers, receipt = self._containers(), self._receipt()
                services = self._capture(references, identity=identity, containers=containers, receipt=receipt)
                auxiliary = self._capture(self._auxiliary_references(config),
                                          identity=identity, containers=[], receipt=receipt)
                by_reference = {}
                for row in auxiliary.values():
                    by_reference.setdefault(row["reference"], set()).add(row["image_id"])
                if any(len(ids) != 1 for ids in by_reference.values()):
                    _fail("baseline_ambiguous", "One auxiliary selector has conflicting baseline image IDs.")
                manifest = {"schema": SCHEMA, "manifest_id": manifest_id, "identity": identity,
                            "owner": owner, "profiles": list(self.profiles), "state": "preparing",
                            "configuration_sha256": self._configuration_hash(config),
                            "services": services, "auxiliary": auxiliary, "excluded_profiles": excluded,
                            "source_receipts_sha256": _digest(receipt),
                            "consumers": {c: {"state": "open"} for c in sorted(set(consumers))},
                            "created_at": datetime.now(timezone.utc).isoformat()}
                namespace = f"{HOLD_REPOSITORY_PREFIX}{_digest(identity)[:24]}:{manifest_id[:24]}-"
                for name, row in [*services.items(), *auxiliary.items()]:
                    row["hold_ref"] = namespace + _digest(name)[:24]
                manifest["binding_sha256"] = _digest({k: manifest[k] for k in IMMUTABLE_FIELDS})
                self._save(manifest)
            return self._protect(manifest) if protect else copy.deepcopy(manifest)

    def _protect(self, manifest):
        self._validate(manifest, holds=manifest["state"] != "preparing")
        if manifest["state"] == "preparing":
            for row in self._rows(manifest):
                observed = self._image(row["hold_ref"], missing_ok=True)
                if observed and observed != row["image_id"]:
                    _fail("hold_conflict", "An owned retention reference already points at another image.")
                if observed is None:
                    self._run(["docker", "image", "tag", row["image_id"], row["hold_ref"]])
                if self._image(row["hold_ref"]) != row["image_id"]:
                    _fail("hold_mismatch", "Retention could not be verified; partial holds remain.")
            manifest["state"] = "protected"
            self._save(manifest)
        return copy.deepcopy(manifest)

    def protect(self, manifest_id):
        with self._locked():
            manifest = self._load(manifest_id)
            if manifest["state"] in {"released", "releasing"}:
                _fail("manifest_released", "Released preservation cannot be reused.")
            return self._protect(manifest)

    def status(self, manifest_id):
        # Atomic reads only: no mkdir, lock, Docker mutation or automatic repair.
        return copy.deepcopy(self._load(manifest_id))

    def _foreign_consumers(self, manifest):
        references = {row["reference"] for row in manifest["auxiliary"].values()}
        for container in self._containers(host=True):
            config = container.get("Config") or {}
            env_refs = {entry.split("=", 1)[1] for entry in config.get("Env") or []
                        if entry.startswith("PY_CODE_EXEC_IMAGE=")}
            if (config.get("Image") in references or env_refs.intersection(references)) and not self._belongs(container, manifest["identity"]):
                _fail("foreign_consumer", "Another deployment consumes the auxiliary image selector.")

    def restore(self, manifest_id, *, start_stack: Callable[[Path], None] | None = None,
                before_disruption: Callable[[], None] | None = None):
        with self._locked():
            manifest = self._load(manifest_id)
            if manifest["state"] not in {"protected", "restoring", "restored"}:
                _fail("manifest_not_protected", "Complete verified protection is required for restore.")
            config = self._validate(manifest, holds=True)
            self._foreign_consumers(manifest)
            for row in manifest["auxiliary"].values():
                if ("@" in row["reference"] or IMAGE_ID.fullmatch(row["reference"])) and self._image(row["reference"]) != row["image_id"]:
                    _fail("auxiliary_reference_mismatch", "An immutable auxiliary selector cannot move.")
            # Startup remains owned by the existing native lifecycle, including
            # its qualified host-vault and transient-credential semantics.
            if start_stack is None:
                _fail("native_start_required", "Restore requires the native platform startup boundary.")
            if before_disruption is not None:
                before_disruption()
            override = self.root / f"{manifest_id}.compose.json"
            _atomic_json(override, {"services": {name: {"image": row["image_id"], "pull_policy": "never"}
                                                  for name, row in manifest["services"].items()}})
            manifest["state"] = "restoring"
            self._save(manifest)
            # Retag only the descriptor-selected mutable auxiliary selector.
            # Service images use immutable Compose overrides. Digests never move.
            for row in manifest["auxiliary"].values():
                if self._image(row["reference"], missing_ok=True) != row["image_id"]:
                    if "@" in row["reference"] or IMAGE_ID.fullmatch(row["reference"]):
                        _fail("auxiliary_reference_mismatch", "An immutable auxiliary selector cannot move.")
                    self._run(["docker", "image", "tag", row["image_id"], row["reference"]])
            self._run([*self._compose(), "down", "--remove-orphans"])
            start_stack(override)
            observed = {}
            for container in self._containers():
                if not self._belongs(container, manifest["identity"]):
                    _fail("restore_attestation_failed", "A restored container belongs to another deployment.")
                name = (container.get("Config") or {}).get("Labels", {}).get("com.docker.compose.service")
                observed.setdefault(name, set()).add(container.get("Image"))
            expected = {name: row["image_id"] for name, row in manifest["services"].items()}
            if set(observed) != set(expected) or any(observed[name] != {value} for name, value in expected.items()):
                _fail("restore_attestation_failed", "The full restored stack does not match every baseline image ID.")
            for row in manifest["auxiliary"].values():
                if self._image(row["reference"]) != row["image_id"]:
                    _fail("restore_attestation_failed", "An auxiliary selector does not resolve to its baseline ID.")
            manifest["state"] = "restored"
            manifest["restored_ids"] = expected
            self._save(manifest)
            return copy.deepcopy(manifest)

    def consumer_done(self, manifest_id, *, consumer: str, evidence_ref: str):
        if not evidence_ref.strip():
            _fail("consumer_evidence_missing", "Closing a consumer requires named evidence.")
        with self._locked():
            manifest = self._load(manifest_id)
            if consumer not in manifest["consumers"]:
                _fail("consumer_unknown", "This preservation lifecycle does not name that consumer.")
            result = {"state": "closed", "evidence_ref": evidence_ref}
            prior = manifest["consumers"][consumer]
            if prior["state"] == "closed" and prior != result:
                _fail("consumer_conflict", "Consumer closure already names different evidence.")
            manifest["consumers"][consumer] = result
            self._save(manifest)
            return copy.deepcopy(manifest)

    def release(self, manifest_id, *, qualification: Mapping[str, Any]):
        with self._locked():
            manifest = self._load(manifest_id)
            expected = {"manifest_id": manifest_id, "binding_sha256": manifest["binding_sha256"],
                        "owner": manifest["owner"], "all_clear": True}
            if (set(qualification) != {*expected, "qualified_by", "evidence_ref"} or
                    any(qualification.get(k) != v for k, v in expected.items()) or
                    not qualification.get("qualified_by") or qualification["qualified_by"] == manifest["owner"] or
                    not qualification.get("evidence_ref")):
                _fail("qualification_invalid", "Release needs bound, independent ALL CLEAR evidence and its owner.")
            if any(value.get("state") != "closed" for value in manifest["consumers"].values()):
                _fail("consumer_open", "A named consumer still requires the baseline images.")
            if self._identity(self._configuration()) != manifest["identity"]:
                _fail("foreign_manifest", "Release belongs to another deployment or Docker daemon.")
            if manifest["state"] in {"releasing", "released"}:
                if manifest.get("release_qualification") != dict(qualification):
                    _fail("release_conflict", "Release already names different qualification evidence.")
                if manifest["state"] == "released":
                    return copy.deepcopy(manifest)
            else:
                # Qualified candidate configuration may differ from the saved
                # baseline. Release checks ownership and exact holds, not the
                # restore-only configuration-compatibility gate.
                self._validate_images(manifest, holds=True)
                manifest["state"] = "releasing"
                manifest["release_qualification"] = dict(qualification)
                self._save(manifest)
            for row in self._rows(manifest):
                observed = self._image(row["hold_ref"], missing_ok=True)
                if observed and observed != row["image_id"]:
                    _fail("hold_conflict", "Release will not remove a foreign retention reference.")
                if observed:
                    self._run(["docker", "image", "rm", row["hold_ref"]])
            manifest["state"] = "released"
            self._save(manifest)
            return copy.deepcopy(manifest)
