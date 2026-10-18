# SPDX-License-Identifier: MIT
"""Application and per-person secrets straight into, and out of, AWS Secrets Manager.

``kdcube config import`` and ``kdcube config export`` use this when the deployment keeps
its secrets in AWS Secrets Manager. The caller's AWS access authorizes the call; no running
runtime and no Card are involved. The documents are exactly the ones the runtime's aws-sm
provider reads and writes (``kdcube_ai_app.infra.secrets.manager.AwsSecretsManagerSecretsManager``):

- ``<prefix>/inventory`` lists every key (schema ``kdcube.aws_secret_inventory.v1``);
- ``<prefix>/platform/secrets``, ``<prefix>/bundles/<id>/secrets``,
  ``<prefix>/users/<uid>/secrets`` and ``<prefix>/users/<uid>/bundles/<id>/secrets``
  each hold one JSON document, nested by the key tail.

Values travel only over the ``aws`` CLI's stdin and stdout: never in its arguments, never on
screen, never in an error.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kdcube_cli.export_live_bundles import aws_cli_env
from kdcube_cli.management.models import ManagementSecretTarget
from kdcube_cli.management.secret_descriptors import (
    SecretDescriptorExport,
    load_secret_descriptors,
    write_secret_descriptors_into_directory,
)
from kdcube_cli.management.secret_export import ExportedSecret

INVENTORY_SCHEMA = "kdcube.aws_secret_inventory.v1"
MAX_INVENTORY_KEYS = 4096
MAX_KEY_CHARS = 1024
MAX_SECRET_STRING_BYTES = 64 * 1024
_INVENTORY_SUFFIX = ".__keys"

WRITE_RULE_OVERWRITE = "overwrite"
WRITE_RULE_WRITE_NEW = "write-new"
WRITE_RULES = (WRITE_RULE_OVERWRITE, WRITE_RULE_WRITE_NEW)

STATUS_NEW = "new"
STATUS_EQUAL = "equal"
STATUS_DIFFERENT = "different"

# Matched pairs: one value that two apps must both hold (Project Board and Connection Hub sign and
# verify each other's requests with it). An import that would leave the two sides different is refused.
_PB = "bundles.problem-board@1-0.secrets."
_CH = "bundles.connection-hub@1-0.secrets."
MATCHED_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("peer proof", _PB + "connection_hub.peer_proof_secret", _CH + "project_membership.peer_proof_secret"),
    (
        "peer receipt",
        _PB + "connection_hub.peer_receipt_secret",
        _CH + "card_transactions.problem_board_receipt_secret",
    ),
    (
        "identity lookup",
        _PB + "connection_hub.identity_lookup_secret",
        _CH + "connections.delegated_credentials.admission.services.problem-board.signing_secret",
    ),
)

PAIR_MATCH = "match"
PAIR_DIFFERS = "differs"
PAIR_ONE_SIDE = "one-side"

AGGREGATE_NONE = "none"
AGGREGATE_ABSENT = "absent"
AGGREGATE_EQUAL = "equal"
AGGREGATE_WOULD_UPDATE = "would-update"
AGGREGATE_UPDATED = "updated"


class AwsSecretsError(RuntimeError):
    """A failure that names documents and keys, never a value."""


class AwsSecretsPartialWrite(AwsSecretsError):
    def __init__(self, message: str, *, written_documents: tuple[str, ...]):
        super().__init__(message)
        self.written_documents = written_documents


# ── The provider's key grammar (manager.py: _split_*_secret_key, validate_secret_provider_key) ──


def _split_bundle_key(key: str) -> tuple[str, str] | None:
    prefix, marker = "bundles.", ".secrets."
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    idx = rest.find(marker)
    if idx < 0:
        return None
    bundle_id = rest[:idx].strip()
    tail = rest[idx + len(marker):].strip()
    if not bundle_id or not tail:
        return None
    return bundle_id, tail


def _split_user_key(key: str) -> tuple[str, str | None, str] | None:
    prefix, marker_bundle, marker_secrets = "users.", ".bundles.", ".secrets."
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    bundle_idx = rest.find(marker_bundle)
    secrets_idx = rest.find(marker_secrets)
    if secrets_idx < 0:
        return None
    if 0 <= bundle_idx < secrets_idx:
        user_id = rest[:bundle_idx].strip()
        remainder = rest[bundle_idx + len(marker_bundle):]
        inner = remainder.find(marker_secrets)
        if inner < 0:
            return None
        bundle_id = remainder[:inner].strip()
        tail = remainder[inner + len(marker_secrets):].strip()
        if not user_id or not bundle_id or not tail:
            return None
        return user_id, bundle_id, tail
    user_id = rest[:secrets_idx].strip()
    tail = rest[secrets_idx + len(marker_secrets):].strip()
    if not user_id or not tail:
        return None
    return user_id, None, tail


def _split_platform_key(key: str) -> str | None:
    if not key.startswith("platform."):
        return None
    return key[len("platform."):].strip() or None


def is_metadata_key(key: str) -> bool:
    """The provider's ``_secret_inventory_prefix`` test: a per-scope ``*.__keys`` listing, never a secret."""
    return str(key or "").strip().endswith(_INVENTORY_SUFFIX)


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not key or key != key.strip() or len(key) > MAX_KEY_CHARS or ".." in key:
        raise AwsSecretsError("A secret key is not a valid provider key.")
    if _split_platform_key(key) is None and _split_bundle_key(key) is None and _split_user_key(key) is None:
        raise AwsSecretsError(f"Secret key {key!r} has no platform, bundle or user scope.")
    return key


def document_id(prefix: str, key: str) -> str:
    """The provider's ``_secret_id``: which grouped document holds ``key``."""
    key = validate_key(key)
    bundle = _split_bundle_key(key)
    if bundle:
        return f"{prefix}/bundles/{bundle[0]}/secrets"
    user = _split_user_key(key)
    if user:
        user_id, bundle_id, _tail = user
        if bundle_id:
            return f"{prefix}/users/{user_id}/bundles/{bundle_id}/secrets"
        return f"{prefix}/users/{user_id}/secrets"
    return f"{prefix}/platform/secrets"


def key_tail(key: str) -> str:
    """The provider's ``_tail_for_key``: the path inside the grouped document."""
    key = validate_key(key)
    bundle = _split_bundle_key(key)
    if bundle:
        return bundle[1]
    user = _split_user_key(key)
    if user:
        return user[2]
    return _split_platform_key(key) or ""


def inventory_id(prefix: str) -> str:
    return f"{prefix}/inventory"


def aggregate_id(prefix: str) -> str:
    """``{app id: that app's whole document}``; ECS injects it as KDCUBE_BUNDLES_SECRETS_JSON."""
    return f"{prefix}/bundles/secrets"


def _parts(path: str) -> list[str]:
    parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    if not parts:
        raise AwsSecretsError("A secret key path is empty.")
    return parts


def set_nested(root: dict[str, Any], path: str, value: str) -> None:
    """The provider's ``_set_nested_value``."""
    parts = _parts(path)
    cursor = root
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


def get_nested(root: Mapping[str, Any], path: str) -> Any:
    cursor: Any = root
    for part in _parts(path):
        if not isinstance(cursor, Mapping) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def serialize_inventory(keys: Iterable[str]) -> str:
    """The provider's ``_serialize_aws_deployment_inventory``."""
    normalized = sorted({validate_key(key) for key in keys if not is_metadata_key(key)})
    if len(normalized) > MAX_INVENTORY_KEYS:
        raise AwsSecretsError("The AWS secret inventory would exceed its key capacity.")
    rendered = json.dumps(
        {"schema": INVENTORY_SCHEMA, "keys": normalized},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(rendered.encode("utf-8")) > MAX_SECRET_STRING_BYTES:
        raise AwsSecretsError("The AWS secret inventory would exceed the secret size limit.")
    return rendered


def parse_inventory(raw: str, *, secret_id: str) -> list[str]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AwsSecretsError(f"{secret_id} is not a valid secret inventory.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != INVENTORY_SCHEMA:
        raise AwsSecretsError(f"{secret_id} is not a valid secret inventory.")
    values = payload.get("keys")
    if not isinstance(values, list) or len(values) > MAX_INVENTORY_KEYS:
        raise AwsSecretsError(f"{secret_id} is not a valid secret inventory.")
    keys: set[str] = set()
    for value in values:
        if not isinstance(value, str) or is_metadata_key(value):
            raise AwsSecretsError(f"{secret_id} is not a valid secret inventory.")
        try:
            keys.add(validate_key(value))
        except AwsSecretsError as exc:
            raise AwsSecretsError(f"{secret_id} is not a valid secret inventory.") from exc
    if len(keys) != len(values):
        raise AwsSecretsError(f"{secret_id} is not a valid secret inventory.")
    return sorted(keys)


def _parse_document(raw: str, *, secret_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AwsSecretsError(f"{secret_id} is not a JSON document.") from exc
    if not isinstance(payload, dict):
        raise AwsSecretsError(f"{secret_id} is not a JSON object.")
    return payload


def _render_document(payload: Mapping[str, Any], *, secret_id: str) -> str:
    rendered = json.dumps(payload, ensure_ascii=False)
    if len(rendered.encode("utf-8")) > MAX_SECRET_STRING_BYTES:
        raise AwsSecretsError(f"{secret_id} would exceed the secret size limit.")
    return rendered


# ── The aws CLI: values only on stdin and stdout ──


Runner = Callable[..., subprocess.CompletedProcess]


class AwsSecretsCli:
    def __init__(self, *, region: str | None, profile: str | None, runner: Runner = subprocess.run):
        self._env = aws_cli_env(region=region, profile=profile)
        self._run = runner

    def _call(self, args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess:
        try:
            return self._run(
                ["aws", "secretsmanager", *args],
                input=stdin,
                capture_output=True,
                text=True,
                check=False,
                env=self._env,
            )
        except FileNotFoundError as exc:
            raise AwsSecretsError("The aws CLI is not installed or not on PATH.") from exc

    @staticmethod
    def _not_found(proc: subprocess.CompletedProcess) -> bool:
        return "ResourceNotFoundException" in str(proc.stderr or "")

    def get(self, secret_id: str) -> str | None:
        """The SecretString of ``secret_id``, or None when it does not exist."""
        proc = self._call(
            ["get-secret-value", "--secret-id", secret_id, "--query", "SecretString", "--output", "json"]
        )
        if proc.returncode != 0:
            if self._not_found(proc):
                return None
            # stderr is not echoed: it is AWS's message, and this module never risks a value on screen.
            raise AwsSecretsError(f"AWS Secrets Manager read failed for {secret_id} (exit {proc.returncode}).")
        try:
            value = json.loads(proc.stdout or "null")
        except ValueError as exc:
            raise AwsSecretsError(f"AWS Secrets Manager returned an unreadable value for {secret_id}.") from exc
        if value is None:
            return None
        if not isinstance(value, str):
            raise AwsSecretsError(f"AWS Secrets Manager returned a non-string value for {secret_id}.")
        return value

    def put(self, secret_id: str, value: str) -> None:
        """Write ``value`` to ``secret_id``, creating the secret when it does not exist yet."""
        proc = self._call(
            ["put-secret-value", "--secret-id", secret_id, "--secret-string", "file:///dev/stdin"],
            stdin=value,
        )
        if proc.returncode == 0:
            return
        if not self._not_found(proc):
            raise AwsSecretsError(f"AWS Secrets Manager write failed for {secret_id} (exit {proc.returncode}).")
        created = self._call(
            ["create-secret", "--name", secret_id, "--secret-string", "file:///dev/stdin"],
            stdin=value,
        )
        if created.returncode != 0:
            raise AwsSecretsError(f"AWS Secrets Manager create failed for {secret_id} (exit {created.returncode}).")


# ── Import ──


@dataclass(frozen=True)
class PairCheck:
    name: str
    status: str  # match | differs | one-side (only one of the two holds a value after the import)


class AwsSecretsPairMismatch(AwsSecretsError):
    def __init__(self, pairs: tuple[PairCheck, ...]):
        names = ", ".join(pair.name for pair in pairs if pair.status == PAIR_DIFFERS)
        super().__init__(
            f"Matched pair(s) would differ after the import: {names}. Both apps must hold the same value; nothing was written."
        )
        self.pairs = pairs


@dataclass(frozen=True)
class KeyPlan:
    key: str
    document: str
    status: str  # new | equal | different

    def action(self, write_rule: str) -> str:
        """What the import does with this key under ``write_rule``."""
        if self.status == STATUS_NEW:
            return "write"
        if self.status == STATUS_EQUAL:
            return "unchanged"
        return "overwrite" if write_rule == WRITE_RULE_OVERWRITE else "skip"


@dataclass(frozen=True)
class ImportResult:
    plan: tuple[KeyPlan, ...]
    written_keys: int = 0
    written_documents: tuple[str, ...] = field(default_factory=tuple)
    # <prefix>/bundles/secrets: none (no app keys), absent, equal, would-update (dry run) or updated
    aggregate: str = AGGREGATE_NONE
    pairs: tuple[PairCheck, ...] = field(default_factory=tuple)


def _status(document: Mapping[str, Any] | None, key: str, value: str) -> str:
    """new, equal or different; a key that would replace a nested block, or nest under a value, is refused."""
    cursor: Any = document or {}
    parts = _parts(key_tail(key))
    for depth, part in enumerate(parts):
        if not isinstance(cursor, Mapping):
            raise AwsSecretsError(
                f"Secret {key!r} would nest under the value at {'.'.join(parts[:depth])!r}; refusing to replace it."
            )
        if part not in cursor or cursor[part] is None:
            return STATUS_NEW
        cursor = cursor[part]
    if isinstance(cursor, (Mapping, list)):
        raise AwsSecretsError(f"Secret {key!r} would replace keys stored below it; refusing.")
    return STATUS_EQUAL if str(cursor) == value else STATUS_DIFFERENT


def _group(prefix: str, values: Mapping[str, str]) -> dict[str, dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for key, value in values.items():
        if is_metadata_key(key):
            continue
        if not isinstance(value, str):
            raise AwsSecretsError(f"Secret {key!r} must be a string value.")
        grouped.setdefault(document_id(prefix, key), {})[key] = value
    return grouped


def check_pairs(
    cli: AwsSecretsCli, prefix: str, values: Mapping[str, str], *, write_rule: str
) -> tuple[PairCheck, ...]:
    """Each matched pair the folder touches, compared as both sides will be after the import. Never a value."""
    documents: dict[str, dict[str, Any]] = {}

    def stored(key: str) -> Any:
        secret_id = document_id(prefix, key)
        if secret_id not in documents:
            raw = cli.get(secret_id)
            documents[secret_id] = {} if raw is None else _parse_document(raw, secret_id=secret_id)
        value = get_nested(documents[secret_id], key_tail(key))
        return None if value is None or isinstance(value, (Mapping, list)) else str(value)

    def after(key: str) -> Any:
        current = stored(key)
        if key in values and (current is None or write_rule == WRITE_RULE_OVERWRITE):
            return values[key]
        return current

    checks: list[PairCheck] = []
    for name, left, right in MATCHED_PAIRS:
        if left not in values and right not in values:
            continue
        left_value, right_value = after(left), after(right)
        if left_value is None or right_value is None:
            checks.append(PairCheck(name=name, status=PAIR_ONE_SIDE))
        else:
            checks.append(PairCheck(name=name, status=PAIR_MATCH if left_value == right_value else PAIR_DIFFERS))
    return tuple(checks)


def plan_import(cli: AwsSecretsCli, prefix: str, values: Mapping[str, str]) -> tuple[KeyPlan, ...]:
    """Compare every key with the stored value in memory: new, equal or different. Writes nothing."""
    plans: list[KeyPlan] = []
    for secret_id, doc_values in sorted(_group(prefix, values).items()):
        raw = cli.get(secret_id)
        document = None if raw is None else _parse_document(raw, secret_id=secret_id)
        for key, value in sorted(doc_values.items()):
            plans.append(KeyPlan(key=key, document=secret_id, status=_status(document, key, value)))
    return tuple(plans)


def apply_import(
    cli: AwsSecretsCli,
    prefix: str,
    values: Mapping[str, str],
    *,
    write_rule: str,
) -> ImportResult:
    """Write the selected keys: the inventory first, then each document re-read just before its write.

    ``overwrite`` writes new and different keys; ``write-new`` writes only keys that have no value yet.
    A matched pair that would differ afterwards refuses the whole import first.
    Equal keys are not rewritten. Then ``<prefix>/bundles/secrets``, where it exists, gets each imported
    app's whole document. The first failed write stops the run and names the documents already
    written, so a rerun is safe; a rerun with nothing left to write still brings the aggregate up to date.
    """
    if write_rule not in WRITE_RULES:
        raise AwsSecretsError(f"--write-rule must be one of {', '.join(WRITE_RULES)}.")
    pairs = check_pairs(cli, prefix, values, write_rule=write_rule)
    if any(pair.status == PAIR_DIFFERS for pair in pairs):
        raise AwsSecretsPairMismatch(pairs)
    plan = plan_import(cli, prefix, values)
    selected = {
        item.key for item in plan if item.action(write_rule) in {"write", "overwrite"}
    }
    grouped = _group(prefix, {key: values[key] for key in selected})

    if selected:
        inventory_secret = inventory_id(prefix)
        raw_inventory = cli.get(inventory_secret)
        inventory = set() if raw_inventory is None else set(parse_inventory(raw_inventory, secret_id=inventory_secret))
        if not selected <= inventory:
            cli.put(inventory_secret, serialize_inventory(inventory | selected))

    written: list[str] = []
    written_keys = 0
    for secret_id, doc_values in sorted(grouped.items()):
        try:
            raw = cli.get(secret_id)
            document = {} if raw is None else _parse_document(raw, secret_id=secret_id)
            changed = 0
            for key, value in sorted(doc_values.items()):
                current = _status(document, key, value)
                if current == STATUS_EQUAL:
                    continue
                if current == STATUS_DIFFERENT and write_rule == WRITE_RULE_WRITE_NEW:
                    continue  # a value appeared since the plan; write-new never replaces one
                set_nested(document, key_tail(key), value)
                changed += 1
            if changed:
                cli.put(secret_id, _render_document(document, secret_id=secret_id))
                written.append(secret_id)
                written_keys += changed
        except AwsSecretsError as exc:
            done = ", ".join(written) if written else "none"
            raise AwsSecretsPartialWrite(
                f"{exc} Stopped; documents already written: {done}. The inventory is updated; rerunning is safe.",
                written_documents=tuple(written),
            ) from exc
    try:
        aggregate_status, aggregate = _plan_aggregate(cli, prefix, values, write_rule)
        if aggregate_status == AGGREGATE_WOULD_UPDATE:
            cli.put(aggregate_id(prefix), _render_document(aggregate, secret_id=aggregate_id(prefix)))
            aggregate_status = AGGREGATE_UPDATED
    except AwsSecretsError as exc:
        done = ", ".join(written) if written else "none"
        raise AwsSecretsPartialWrite(
            f"{exc} Stopped before {aggregate_id(prefix)}; documents already written: {done}. "
            "Rerunning is safe and brings the aggregate up to date.",
            written_documents=tuple(written),
        ) from exc
    return ImportResult(
        plan=plan,
        written_keys=written_keys,
        written_documents=tuple(written),
        aggregate=aggregate_status,
        pairs=pairs,
    )


def _clean(value: Any) -> Any:
    """sync_secrets.py's ``clean``: the aggregate carries no empty or null leaf."""
    if isinstance(value, Mapping):
        cleaned = {key: _clean(child) for key, child in value.items()}
        return {key: child for key, child in cleaned.items() if child is not None}
    if value is None or value == "":
        return None
    return value


def _plan_aggregate(
    cli: AwsSecretsCli, prefix: str, values: Mapping[str, str], write_rule: str
) -> tuple[str, dict[str, Any] | None]:
    """The aggregate with every imported app's document as it is (or, in a dry run, will be) after the import.

    It is updated only where it exists: the deployment creates it; the CLI never does.
    """
    by_app: dict[str, dict[str, str]] = {}
    for key, value in values.items():
        bundle = None if is_metadata_key(key) else _split_bundle_key(key)
        if bundle:
            by_app.setdefault(bundle[0], {})[key] = value
    if not by_app:
        return AGGREGATE_NONE, None
    raw = cli.get(aggregate_id(prefix))
    if raw is None:
        return AGGREGATE_ABSENT, None
    current = _parse_document(raw, secret_id=aggregate_id(prefix))
    updated = dict(current)
    for bundle_id, app_values in sorted(by_app.items()):
        secret_id = f"{prefix}/bundles/{bundle_id}/secrets"
        raw_document = cli.get(secret_id)
        document = {} if raw_document is None else _parse_document(raw_document, secret_id=secret_id)
        for key, value in sorted(app_values.items()):
            status = _status(document, key, value)
            if status == STATUS_NEW or (status == STATUS_DIFFERENT and write_rule == WRITE_RULE_OVERWRITE):
                set_nested(document, key_tail(key), value)
        updated[bundle_id] = _clean(document) or {}
    if updated == current:
        return AGGREGATE_EQUAL, None
    return AGGREGATE_WOULD_UPDATE, updated


def plan_aggregate(cli: AwsSecretsCli, prefix: str, values: Mapping[str, str], *, write_rule: str) -> str:
    """For a dry run: none, absent, equal or would-update. Writes nothing."""
    return _plan_aggregate(cli, prefix, values, write_rule)[0]


# ── Export ──


def export_values(cli: AwsSecretsCli, prefix: str, *, include_platform: bool) -> dict[str, str]:
    """Every inventory key and its value, read document by document. A missing document or value stops."""
    inventory_secret = inventory_id(prefix)
    raw_inventory = cli.get(inventory_secret)
    if raw_inventory is None:
        raise AwsSecretsError(f"{inventory_secret} does not exist: nothing is recorded under {prefix}.")
    keys = [
        key
        for key in parse_inventory(raw_inventory, secret_id=inventory_secret)
        if include_platform or _split_platform_key(key) is None
    ]
    grouped: dict[str, list[str]] = {}
    for key in keys:
        grouped.setdefault(document_id(prefix, key), []).append(key)
    values: dict[str, str] = {}
    missing: list[str] = []
    for secret_id, doc_keys in sorted(grouped.items()):
        raw = cli.get(secret_id)
        if raw is None:
            raise AwsSecretsError(
                f"The inventory names {len(doc_keys)} key(s) in {secret_id}, which does not exist."
            )
        document = _parse_document(raw, secret_id=secret_id)
        for key in sorted(doc_keys):
            value = get_nested(document, key_tail(key))
            if value is None or isinstance(value, (Mapping, list)):
                missing.append(key)
                continue
            values[key] = value if isinstance(value, str) else str(value)
    if missing:
        listed = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
        raise AwsSecretsError(f"The inventory names keys that have no value: {listed}.")
    return values


# ── Descriptor folders ──


def import_values_from_descriptors(directory: Path, *, include_platform: bool) -> dict[str, str]:
    """Provider keys and values from an exported folder (bundles.secrets.yaml, and secrets.yaml when present).

    secrets.yaml carries the per-person secrets under ``users:``, so it is read whenever it exists;
    its ``platform:`` keys are kept only with ``include_platform``.
    """
    has_secrets_yaml = (directory / "secrets.yaml").exists()
    loaded = load_secret_descriptors(directory, include_platform=has_secrets_yaml, allow_empty=True)
    values: dict[str, str] = {}
    for item in loaded.values:
        if item.target.scope == "platform" and not include_platform:
            continue
        values[item.target.provider_key] = item.value
    return values


def write_values_to_descriptors(
    directory: Path, values: Mapping[str, str], *, replace: bool
) -> SecretDescriptorExport:
    return write_secret_descriptors_into_directory(
        directory,
        [ExportedSecret(target=ManagementSecretTarget.from_provider_key(key), value=value) for key, value in values.items()],
        replace=replace,
    )


def plan_label(item: KeyPlan, write_rule: str) -> str:
    """The dry-run word for one key: new, equal, would-overwrite (overwrite) or skipped (write-new)."""
    if item.status != STATUS_DIFFERENT:
        return item.status
    return "would-overwrite" if write_rule == WRITE_RULE_OVERWRITE else "skipped"


__all__ = [
    "AwsSecretsCli",
    "AwsSecretsError",
    "AwsSecretsPairMismatch",
    "AwsSecretsPartialWrite",
    "MATCHED_PAIRS",
    "PAIR_DIFFERS",
    "PairCheck",
    "check_pairs",
    "ImportResult",
    "KeyPlan",
    "WRITE_RULES",
    "WRITE_RULE_OVERWRITE",
    "WRITE_RULE_WRITE_NEW",
    "apply_import",
    "document_id",
    "export_values",
    "import_values_from_descriptors",
    "plan_label",
    "write_values_to_descriptors",
    "key_tail",
    "plan_aggregate",
    "plan_import",
    "serialize_inventory",
]
