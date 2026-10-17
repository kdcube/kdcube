# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""User props import through delegated KDCube management (W890).

Operator, 2026-10-16 (option A, "5. A agree"): after a deploy, ``kdcube config import --include-user-data``
writes the exported user props into the running runtime: insert, never overwrite, and report equal and
different rows. Operator, 2026-10-17: Connection Hub's own props are never exported ("3. exclude"), so they
are never imported either.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

USER_PROPS_IMPORT_OPERATION = "kdcube.management.user_props.import"
USER_PROPS_RESOURCE_SELECTOR = "urn:kdcube:management:user-props:*:*"
USER_PROPS_IMPORT_SCHEMA = "kdcube.management.user_props.import.v1"
# Connection Hub's records about the tokens it acquires: never imported.
EXCLUDED_USER_PROP_PREFIXES = ("delegated_to_kdcube.",)
MAX_USER_PROPS_PER_REQUEST = 500
MAX_USER_PROP_VALUE_BYTES = 64 * 1024
MAX_USER_PROPS_REQUEST_BYTES = 4 * 1024 * 1024  # the CLI batches by rows and by bytes
_MAX_TEXT = 512


class UserPropsRequestInvalid(ValueError):
    pass


def user_props_resource(*, tenant: str, project: str) -> str:
    encoded = [quote(str(item or "").strip(), safe="-._~") for item in (tenant, project)]
    if not all(encoded):
        raise UserPropsRequestInvalid("user props resource identity is incomplete")
    return "urn:kdcube:management:user-props:" + ":".join(encoded)


def _text(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise UserPropsRequestInvalid(f"{name} must be text")
    text = value.strip()
    if not text or text != value or len(text) > _MAX_TEXT or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise UserPropsRequestInvalid(f"{name} must be one exact non-empty value")
    return text


@dataclass(frozen=True)
class UserPropRow:
    user_id: str
    bundle_id: str
    key: str
    subsystem: str
    value_json: str  # canonical JSON text

    @classmethod
    def from_mapping(cls, value: Any) -> UserPropRow:
        if not isinstance(value, Mapping) or set(value) != {"user_id", "bundle_id", "key", "subsystem", "value_json"}:
            raise UserPropsRequestInvalid("every row needs exactly user_id, bundle_id, key, subsystem and value_json")
        key = _text(value["key"], name="key")
        if key.startswith(EXCLUDED_USER_PROP_PREFIXES):
            raise UserPropsRequestInvalid("Connection Hub's own props are never imported")
        raw = value["value_json"]
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_USER_PROP_VALUE_BYTES:
            raise UserPropsRequestInvalid("value_json must be JSON text within the size limit")
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise UserPropsRequestInvalid("value_json must be JSON text") from None
        if parsed is None:
            raise UserPropsRequestInvalid("value_json must not be null")
        return cls(
            user_id=_text(value["user_id"], name="user_id"),
            bundle_id=_text(value["bundle_id"], name="bundle_id"),
            key=key,
            subsystem=_text(value["subsystem"], name="subsystem"),
            value_json=json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )

    def public_dict(self) -> dict[str, str]:
        return {"user_id": self.user_id, "bundle_id": self.bundle_id, "key": self.key,
                "subsystem": self.subsystem, "value_json": self.value_json}


def parse_user_props_import(payload: Mapping[str, Any]) -> tuple[UserPropRow, ...]:
    """The exact rows of one import request; any invalid row, a duplicate or a Connection Hub prop refuses
    the whole request before admission."""
    if set(payload) != {"schema", "rows"} or payload.get("schema") != USER_PROPS_IMPORT_SCHEMA:
        raise UserPropsRequestInvalid("the request needs exactly schema and rows")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= MAX_USER_PROPS_PER_REQUEST:
        raise UserPropsRequestInvalid(f"rows must hold 1 to {MAX_USER_PROPS_PER_REQUEST} props")
    rows = tuple(UserPropRow.from_mapping(row) for row in raw_rows)
    if len({(row.user_id, row.bundle_id, row.key) for row in rows}) != len(rows):
        raise UserPropsRequestInvalid("a prop appears twice in one request")
    return rows


def import_body(rows: Sequence[UserPropRow]) -> dict[str, Any]:
    """The canonical body the request digest binds: approval and replay cover exactly these rows."""
    return {"schema": USER_PROPS_IMPORT_SCHEMA,
            "rows": [row.public_dict() for row in sorted(rows, key=lambda r: (r.user_id, r.bundle_id, r.key))]}


def approval_context(rows: Sequence[UserPropRow]) -> dict[str, str]:
    """What the person approving sees: counts only, never a value."""
    return {"user_props_rows": str(len(rows)),
            "user_props_users": str(len({row.user_id for row in rows})),
            "user_props_applications": str(len({row.bundle_id for row in rows}))}


__all__ = [
    "EXCLUDED_USER_PROP_PREFIXES",
    "MAX_USER_PROPS_PER_REQUEST",
    "MAX_USER_PROPS_REQUEST_BYTES",
    "MAX_USER_PROP_VALUE_BYTES",
    "USER_PROPS_IMPORT_OPERATION",
    "USER_PROPS_IMPORT_SCHEMA",
    "USER_PROPS_RESOURCE_SELECTOR",
    "UserPropRow",
    "UserPropsRequestInvalid",
    "approval_context",
    "import_body",
    "parse_user_props_import",
    "user_props_resource",
]
