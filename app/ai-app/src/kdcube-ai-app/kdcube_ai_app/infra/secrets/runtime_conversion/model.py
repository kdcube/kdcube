# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Private, pinned inputs for explicit offline runtime-record conversion."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from kdcube_ai_app.infra.secrets.host_vault.protocol import MAX_VALUE_BYTES
from kdcube_ai_app.infra.secrets.runtime_contract import valid_namespace
from kdcube_ai_app.infra.secrets.runtime_vault import SCHEMA


class ConversionError(RuntimeError):
    """A fixed value-free refusal; callers log only this code, never locals."""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8", "strict")


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def strict_json(value: bytes):
    return json.loads(value.decode("utf-8", "strict"), object_pairs_hook=unique_fields,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


@dataclass(frozen=True, repr=False)
class SourceRecord:
    namespace: str
    secret_ref: str
    generation: int
    state: str                    # present, absent, tombstone
    value: bytes | None = None     # exact original inner UTF-8, never reserialized
    expires_at: int | None = None

    @property
    def key(self) -> str:
        return f"platform.runtime.{self.namespace}.{self.secret_ref}"


@dataclass(frozen=True, repr=False)
class Observation:
    generation: int
    state: str
    value: bytes | None = None


class FamilyParser(Protocol):
    """Source-reviewed, pure family codec, pinned by trusted composition.

    validate must reject unknown fields and verify the exact inner family,
    namespace/reference/scope binding. There are deliberately no default
    parsers: the host must supply every required family's reviewed codec.
    """
    source_sha256: str

    def validate(self, payload: dict, *, namespace: str, secret_ref: str,
                 scope: tuple[str, str, str]) -> int: ...


@dataclass(frozen=True, repr=False)
class IndexRecord:
    """Original namespace index: always preserved, never converted/rebuilt."""
    namespace: str
    generation: int
    state: str
    value: bytes | None = None

    def as_source(self) -> SourceRecord:
        return SourceRecord(self.namespace, "__keys", self.generation, self.state, self.value)


@dataclass(frozen=True, repr=False)
class Inventory:
    scope: tuple[str, str, str]     # tenant, project, application
    records: tuple[SourceRecord, ...]
    parser_pins: tuple[tuple[str, str], ...]
    binding_pins: tuple[tuple[str, str], ...] = ()
    index_records: tuple[IndexRecord, ...] = ()

    def sha256(self) -> str:
        return digest(canonical({
            "scope": self.scope, "parser_pins": self.parser_pins, "binding_pins": self.binding_pins,
            "records": [{"namespace": r.namespace, "secret_ref": r.secret_ref,
                         "generation": r.generation, "state": r.state,
                         "inner_sha256": digest(r.value) if type(r.value) is bytes else None,
                         "expires_at": r.expires_at} for r in self.records],
            "indexes": [{"namespace": r.namespace, "generation": r.generation,
                         "state": r.state, "value_sha256": digest(r.value) if type(r.value) is bytes else None}
                        for r in self.index_records],
        }))


@dataclass(frozen=True, repr=False)
class PreparedRecord:
    source: SourceRecord
    wrapper: bytes | None


def preflight(inventory: Inventory, parsers: dict[str, FamilyParser]) -> tuple[PreparedRecord, ...]:
    """Validate the entire inventory and every outer size before any key access."""
    try:
        if (type(inventory.scope) is not tuple or len(inventory.scope) != 3
                or any(type(v) is not str or not v for v in inventory.scope)
                or type(inventory.records) is not tuple or len(inventory.records) > 10000
                or type(inventory.parser_pins) is not tuple
                or type(inventory.binding_pins) is not tuple or type(inventory.index_records) is not tuple
                or not inventory.records and not inventory.index_records):
            raise ValueError
        binding_pins = dict(inventory.binding_pins)
        bound_names = {ns for ns, parser in parsers.items() if hasattr(parser, "binding_sha256")}
        if (len(binding_pins) != len(inventory.binding_pins) or set(binding_pins) != bound_names
                or any(type(pin) is not str or re.fullmatch(r"[0-9a-f]{64}", pin) is None
                       or parsers[ns].binding_sha256 != pin for ns, pin in binding_pins.items())):
            raise ValueError
        pins = dict(inventory.parser_pins)
        if (len(pins) != len(inventory.parser_pins) or not pins
                or set(pins) != set(parsers)
                or any(not valid_namespace(ns) or type(pin) is not str
                       or re.fullmatch(r"[0-9a-f]{64}", pin) is None
                       or parsers[ns].source_sha256 != pin for ns, pin in pins.items())):
            raise ValueError
        seen, prepared = set(), []
        for record in inventory.records:
            if (type(record) is not SourceRecord or not valid_namespace(record.namespace)
                    or record.namespace not in pins or type(record.secret_ref) is not str
                    or re.fullmatch(r"[0-9a-f]{32}", record.secret_ref) is None
                    or record.key in seen or type(record.generation) is not int
                    or record.state not in {"present", "absent", "tombstone"}):
                raise ValueError
            seen.add(record.key)
            if record.state != "present":
                if record.value is not None or record.expires_at is not None:
                    raise ValueError
                if ((record.state == "absent" and record.generation != 0)
                        or (record.state == "tombstone" and record.generation <= 0)):
                    raise ValueError
                prepared.append(PreparedRecord(record, None))
                continue
            if (record.generation <= 0 or type(record.value) is not bytes
                    or len(record.value) > MAX_VALUE_BYTES or type(record.expires_at) is not int
                    or record.expires_at <= 0):
                raise ValueError
            payload = strict_json(record.value)
            if type(payload) is not dict:
                raise ValueError
            deadline = parsers[record.namespace].validate(
                payload, namespace=record.namespace, secret_ref=record.secret_ref, scope=inventory.scope)
            if type(deadline) is not int or deadline != record.expires_at:
                raise ValueError
            wrapper = canonical({"schema": SCHEMA, "namespace": record.namespace,
                                 "secret_ref": record.secret_ref,
                                 "value": record.value.decode("utf-8", "strict"),
                                 "expires_at": record.expires_at})
            if len(wrapper) > MAX_VALUE_BYTES:
                raise ValueError
            prepared.append(PreparedRecord(record, wrapper))
        index_names = set()
        for index in inventory.index_records:
            if (type(index) is not IndexRecord or not valid_namespace(index.namespace) or index.namespace not in pins
                    or index.namespace in index_names or type(index.generation) is not int
                    or index.state not in {"present", "absent", "tombstone"}):
                raise ValueError
            index_names.add(index.namespace)
            if index.state == "present":
                if (index.generation <= 0 or type(index.value) is not bytes
                        or len(index.value) > MAX_VALUE_BYTES):
                    raise ValueError
            elif (index.value is not None or index.state == "absent" and index.generation != 0
                  or index.state == "tombstone" and index.generation <= 0):
                raise ValueError
            prepared.append(PreparedRecord(index.as_source(), None))
        if not bound_names <= index_names:
            raise ValueError
        return tuple(prepared)
    except Exception:
        raise ConversionError("runtime_conversion_preflight_refused") from None
