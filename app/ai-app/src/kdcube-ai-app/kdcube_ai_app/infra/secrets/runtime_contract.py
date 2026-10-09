# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Mode-neutral runtime custody qualification and service scope authorization."""
from __future__ import annotations

import hashlib
import json
import re
import os
import plistlib
import subprocess
from pathlib import Path

SCHEMA = "kdcube.runtime_secret_contract.v1"
POLICY_SCHEMA = "kdcube.runtime_secret_scopes.v1"
_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
# W673 (W670 regression, operator 2026-10-09: "it simply must work regardless of the backend"): the
# platform's own runtime-record purposes, enrolled when a deployment declares no secrets.runtime.namespaces.
# An explicit list, an empty one included, is the operator's choice and wins. "users" is reserved for
# per-user secret folders and is never a purpose here.
DEFAULT_RUNTIME_NAMESPACES = ("login-attempts", "card-credentials", "oauth-refresh-tokens")


def runtime_section_namespaces(section: object) -> tuple[str, ...]:
    """The enrolled namespaces from the assembly ``secrets.runtime`` section, the CLI projection's rule.

    Absent section, or a section whose ``namespaces`` is omitted or null: the platform defaults (Main,
    2026-10-09: "omitted namespaces => the 3 defaults in BOTH, explicit [] => none in both"). An explicit
    list, the empty one included, is exact. Anything malformed enrolls nothing.
    """
    if section is None:
        return DEFAULT_RUNTIME_NAMESPACES
    if not isinstance(section, dict):
        return ()
    namespaces = section.get("namespaces")
    if namespaces is None:
        return DEFAULT_RUNTIME_NAMESPACES
    return tuple(namespaces) if isinstance(namespaces, (list, tuple)) else ()


GUARANTEES = frozenset({
    "create_only", "restart_persistent", "expiry_enforced",
    "bounded_atomic_purge", "scope_authorized",
})


def valid_namespace(namespace: object) -> bool:
    return type(namespace) is str and _NAMESPACE.fullmatch(namespace) is not None


def qualification(namespace: str) -> dict:
    return {"schema": SCHEMA, "namespace": namespace, **{key: True for key in GUARANTEES}}


def qualified(payload: object, *, namespace: str) -> bool:
    return (type(payload) is dict and set(payload) == {"schema", "namespace"} | GUARANTEES
            and payload["schema"] == SCHEMA and valid_namespace(namespace)
            and payload["namespace"] == namespace
            and all(payload[key] is True for key in GUARANTEES))


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def persistent_filesystem(root: Path) -> bool:
    """Refuse known transient/unknown storage; not a mount-lifecycle verdict.

    Linux's closest mount and macOS's actual volume determine this check. A
    deployment must still prove its volume survives container/host replacement.

    Docker Desktop shows a folder shared from the host as mount type
    ``fakeowner`` with a source under ``/run/host_mark/``; the host's disk
    backs it. Exactly that pairing is accepted. A ``fakeowner`` mount from any
    other source, and a transient mount nested inside a host share, are not.
    """
    durable_types = {"apfs", "hfs", "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "ufs"}

    def unescape(field: str) -> str:
        for old, new in (("\\040", " "), ("\\011", "\t"), ("\\134", "\\")):
            field = field.replace(old, new)
        return field

    try:
        if os.uname().sysname == "Linux":
            matches = []
            target = str(root.resolve())
            for line in Path("/proc/self/mountinfo").read_text().splitlines():
                left, right = line.split(" - ", 1)
                mount = unescape(left.split()[4])
                if target == mount or target.startswith(mount.rstrip("/") + "/"):
                    fs_type, source = right.split()[:2]
                    matches.append((len(mount), fs_type, unescape(source)))
            if not matches:
                return False
            _, fs_type, source = max(matches)
            return fs_type in durable_types or (
                fs_type == "fakeowner" and source.startswith("/run/host_mark/"))
        if os.uname().sysname == "Darwin":
            disk = subprocess.run(["/bin/df", "-P", str(root)], capture_output=True, timeout=2, check=True)
            device = disk.stdout.decode().splitlines()[1].split()[0]
            if not re.fullmatch(r"/dev/disk[0-9]+(?:s[0-9]+)*", device):
                return False
            result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", device],
                                    capture_output=True, timeout=2, check=True)
            info = plistlib.loads(result.stdout)
            return (info.get("FilesystemType") in durable_types
                    and info.get("BusProtocol") not in {"Disk Image", "Virtual Interface"})
    except (OSError, ValueError, UnicodeError, IndexError, subprocess.SubprocessError):
        return False
    return False


class RuntimeScopePolicy:
    """Host-owned SHA-256 credential identifiers mapped to exact namespaces.

    A namespace is an address, not a grant. Unconfigured or malformed policy
    grants no access. Caller headers never supply the policy or its scopes.
    This does not isolate code that already shares the host's credential/UID.
    """

    def __init__(self, raw: str | None):
        self._grants = {"read": {}, "write": {}}
        try:
            if type(raw) is not str or len(raw.encode("utf-8")) > 65536:
                return
            payload = json.loads(raw, object_pairs_hook=_unique_fields)
            if (type(payload) is not dict or set(payload) != {"schema", "read", "write"}
                    or payload["schema"] != POLICY_SCHEMA):
                return
            for kind in ("read", "write"):
                grants = payload[kind]
                if type(grants) is not dict or len(grants) > 64:
                    return
                for digest, namespaces in grants.items():
                    if (type(digest) is not str or _DIGEST.fullmatch(digest) is None
                            or type(namespaces) is not list or len(namespaces) > 64
                            or any(not valid_namespace(value) for value in namespaces)):
                        return
            self._grants = payload
        except (ValueError, UnicodeError, TypeError, RecursionError):
            return

    def authorized(self, *, namespace: str, token: str | None, kind: str) -> bool:
        if (not valid_namespace(namespace) or type(token) is not str or not token
                or len(token) > 4096 or type(kind) is not str or kind not in {"read", "write"}):
            return False
        try:
            encoded = token.encode("utf-8")
        except UnicodeError:
            return False
        if len(encoded) > 4096:
            return False
        digest = hashlib.sha256(encoded).hexdigest()
        return namespace in self._grants[kind].get(digest, ())


def runtime_key_namespace(key: str) -> str | None:
    if type(key) is not str or key == "platform.runtime":
        return ""
    if not key.startswith("platform.runtime."):
        return None
    parts = key.split(".")
    return parts[2] if len(parts) == 4 and valid_namespace(parts[2]) else ""
