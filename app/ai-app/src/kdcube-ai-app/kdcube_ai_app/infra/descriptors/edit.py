# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Targeted edits of the staged descriptor files.

Why this exists: the platform reads its sign-in authority and the sign-in
providers from the staged ``assembly.yaml`` and ``bundles.yaml`` (see
``config_scopes._load_assembly_plain`` and ``_load_bundles_plain``), and an
administrator wants to change exactly those keys from Connection Hub. The
files are the single place of truth on a runtime, they carry the operator's
comments, and every other key must survive untouched. So an edit here is:
the one node the caller names, replaced through a comment-preserving
round-trip, written to a temporary file and moved into place, with the
previous file kept beside it as ``<name>.bak-<UTC stamp>``.

What it never does: read or write a secret value. Keys that carry a secret
reference or a cookie name are merged from the existing file whenever the
caller submits the literal ``<unchanged>`` for them, and a submission that
drops such a key is refused unless the caller says so.

Activation is not this module's business. A changed provider or pool is
picked up by readers that re-read the file (the loaders cache by mtime) and
by services that rebuild their auth manager; the lane switch applies on a
runtime refresh. The caller tells the administrator which.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.config_scopes import _descriptor_path, _resolve_dotted_value
from kdcube_ai_app.infra.descriptors.locking import descriptor_edit_lock

UNCHANGED = "<unchanged>"
SECRET_LIKE_KEYS = frozenset({"id_token", "cookie", "client_secret", "secret", "secret_ref", "token"})
EDITING_POLICY_PATH = "management.platform_settings.editing"
DEFAULT_CONNECTION_HUB_BUNDLE = "connection-hub@1-0"

# The provider types the platform resolver understands. Every row here lives
# in an app registry, so assembly.yaml uses ``auth.type: bundle`` for the hop;
# the resolved provider type tells the frontend how sign-in actually runs.
AUTH_TYPE_FOR_PROVIDER_TYPE = {
    "bundle": "bundle",
    "multi_cognito": "bundle",
    "multi-cognito": "bundle",
    "cognito": "bundle",
    "cognito_id_token": "bundle",
    "simple_idp": "bundle",
    "simple-idp": "bundle",
    "simple": "bundle",
}
COGNITO_TYPES = frozenset({"cognito", "multi_cognito", "multi-cognito", "cognito_id_token"})
BUNDLE_LOGIN_TYPES = frozenset({"bundle"})
RETIRED_BUNDLE_LOGIN_TYPES = frozenset(
    {"bundle_session_login", "bundle-session-login", "bundle_session", "bundle-session", "session"}
)


class DescriptorEditRefused(RuntimeError):
    """The edit was not made. ``reason`` is stable and machine-readable."""

    def __init__(self, reason: str, message: str, *, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.problems = list(problems or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "descriptor_edit_refused",
            "reason": self.reason,
            "message": self.message,
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class DescriptorEdit:
    path: str
    backup: str
    changed: tuple[str, ...]
    scope: str
    activation: str
    problems: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "backup": self.backup,
            "changed": list(self.changed),
            "scope": self.scope,
            "activation": self.activation,
            "problems": list(self.problems),
        }


def assembly_path() -> Path:
    return _descriptor_path(env_name="ASSEMBLY_YAML_DESCRIPTOR_PATH", filename="assembly.yaml", default="/config/assembly.yaml")


def bundles_path() -> Path:
    return _descriptor_path(env_name="BUNDLES_YAML_DESCRIPTOR_PATH", filename="bundles.yaml", default="/config/bundles.yaml")


def platform_settings_editing_policy(*, path: Path | None = None) -> dict[str, Any]:
    """Read the descriptor-owned editing gate from ``assembly.yaml``.

    The gate is closed unless both the general switch and the named section
    switch are explicitly true. This leaves room for later platform settings
    without implicitly exposing their writers when the first section is
    enabled.
    """
    target = path or assembly_path()
    if not target.exists():
        return {"enabled": False, "sections": {}}
    policy = _resolve_dotted_value(_plain(_load_rt(target)), EDITING_POLICY_PATH)
    if not isinstance(policy, Mapping):
        return {"enabled": False, "sections": {}}
    sections = policy.get("sections")
    return {
        "enabled": policy.get("enabled") is True,
        "sections": dict(sections) if isinstance(sections, Mapping) else {},
    }


def edits_enabled(*, section: str = "auth", policy_path: Path | None = None) -> bool:
    policy = platform_settings_editing_policy(path=policy_path)
    sections = policy.get("sections") or {}
    return policy.get("enabled") is True and sections.get(str(section or "").strip()) is True


def _guard(path: Path, *, section: str, policy_path: Path | None = None) -> None:
    if not edits_enabled(section=section, policy_path=policy_path):
        raise DescriptorEditRefused(
            "editing_disabled",
            f"Editing the `{section}` platform settings is disabled in `{EDITING_POLICY_PATH}`. Change the source descriptor and publish it.",
        )
    if not path.exists():
        raise DescriptorEditRefused("file_missing", f"The descriptor file is not here: {path}")
    if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
        raise DescriptorEditRefused("not_writable", f"The descriptor file is not writable from this runtime: {path}")


def _yaml():
    from ruamel.yaml import YAML

    rt = YAML(typ="rt")
    rt.preserve_quotes = True
    rt.width = 4096
    return rt


def _load_rt(path: Path) -> Any:
    return _yaml().load(path.read_text(encoding="utf-8"))


def _backup_name(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.bak-{stamp}")


def _write_rt(path: Path, document: Any) -> Path:
    """Keep the previous file beside the new one, write the new one to a
    temporary file in the same directory, then move it into place."""
    backup = _backup_name(path)
    shutil.copy2(path, backup)
    buffer = io.StringIO()
    _yaml().dump(document, buffer)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(buffer.getvalue())
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copymode(path, tmp_name)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return backup


def _is_secret_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    return lowered in SECRET_LIKE_KEYS or lowered.endswith("_ref")


def merge_unchanged(submitted: Any, existing: Any) -> Any:
    """Replace every ``<unchanged>`` in ``submitted`` with the value the
    existing node holds at the same place. Mappings recurse; anything else
    is taken as submitted."""
    if isinstance(submitted, str) and submitted == UNCHANGED:
        return existing
    if isinstance(submitted, Mapping):
        existing_map = existing if isinstance(existing, Mapping) else {}
        return {str(k): merge_unchanged(v, existing_map.get(str(k))) for k, v in submitted.items()}
    if isinstance(submitted, list):
        existing_list = existing if isinstance(existing, list) else []
        return [
            merge_unchanged(item, existing_list[index] if index < len(existing_list) else None)
            for index, item in enumerate(submitted)
        ]
    return submitted


def dropped_secret_keys(submitted: Any, existing: Any, prefix: str = "") -> list[str]:
    """Secret-bearing keys the existing node has and the submission lacks."""
    if not isinstance(existing, Mapping):
        return []
    submitted_map = submitted if isinstance(submitted, Mapping) else {}
    dropped: list[str] = []
    for key, value in existing.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if str(key) not in submitted_map:
            if _is_secret_key(str(key)) or (isinstance(value, Mapping) and any(_is_secret_key(str(k)) for k in value)):
                dropped.append(dotted)
            continue
        dropped.extend(dropped_secret_keys(submitted_map.get(str(key)), value, dotted))
    return dropped


def validate_authority_provider(provider: Any) -> list[str]:
    """What a sign-in provider block must carry to resolve. Plain sentences,
    one per problem, empty when the block is acceptable."""
    problems: list[str] = []
    if not isinstance(provider, Mapping):
        return ["The provider must be a mapping (a YAML block with keys), not a list or a scalar."]
    provider_type = str(provider.get("type") or "").strip().lower()
    if not provider_type:
        problems.append("The provider needs a `type`.")
        return problems
    if provider_type in RETIRED_BUNDLE_LOGIN_TYPES:
        problems.append(f"Provider type `{provider_type}` was removed. Use `bundle`.")
        return problems
    if provider_type not in AUTH_TYPE_FOR_PROVIDER_TYPE:
        problems.append(
            f"Unknown provider type `{provider_type}`. The platform resolves: cognito, multi_cognito, bundle, simple_idp."
        )
        return problems
    enabled = provider.get("enabled", True)
    if not isinstance(enabled, bool):
        problems.append("`enabled` must be true or false.")
    if provider_type in COGNITO_TYPES:
        authenticator = provider.get("authenticator")
        source = authenticator if isinstance(authenticator, Mapping) else provider
        for key, alt in (("region", ()), ("user_pool_id", ("pool_id",)), ("app_client_id", ("client_id",))):
            if not any(str(source.get(k) or "").strip() for k in (key, *alt)):
                problems.append(f"A Cognito provider needs `authenticator.{key}`.")
        trusted = source.get("trusted_providers") if isinstance(source, Mapping) else None
        if trusted is not None:
            if not isinstance(trusted, list):
                problems.append("`trusted_providers` must be a list of pools.")
            else:
                for index, row in enumerate(trusted):
                    if not isinstance(row, Mapping):
                        problems.append(f"trusted_providers[{index}] must be a mapping.")
                        continue
                    for key in ("alias", "region", "user_pool_id", "app_client_id"):
                        if not str(row.get(key) or "").strip():
                            problems.append(f"trusted_providers[{index}] needs `{key}`.")
    elif provider_type in BUNDLE_LOGIN_TYPES:
        input_cfg = provider.get("input")
        ref = input_cfg.get("authenticator_ref") if isinstance(input_cfg, Mapping) else None
        if not isinstance(ref, Mapping) or not str(ref.get("provider_id") or ref.get("authenticator_id") or "").strip():
            problems.append("A bundle login provider needs `input.authenticator_ref.provider_id`: the authenticator it signs in through.")
        issuer = provider.get("issuer")
        if not isinstance(issuer, Mapping) or not str(issuer.get("type") or "").strip():
            problems.append("A bundle login provider needs `issuer.type` (kdcube_session_token).")
    return problems


def _find_bundle_entry(document: Any, bundle_id: str) -> Any:
    """The bundle's node, the way the runtime's loader looks for it: a direct
    key, then an entry of `bundles.items` (a list of `{id: ...}` items, or a
    mapping keyed by id), then a key under `bundles`."""
    if not isinstance(document, Mapping):
        return None
    if bundle_id in document and isinstance(document.get(bundle_id), Mapping):
        return document[bundle_id]
    bundles = document.get("bundles")
    if isinstance(bundles, Mapping):
        items = bundles.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, Mapping) and str(item.get("id") or "").strip() == bundle_id:
                    return item
        elif isinstance(items, Mapping) and isinstance(items.get(bundle_id), Mapping):
            return items[bundle_id]
        if isinstance(bundles.get(bundle_id), Mapping):
            return bundles[bundle_id]
    return None


def _bundle_props_node(entry: Any) -> Any:
    """Return the physical node exposed as bundle properties at runtime."""
    if not isinstance(entry, Mapping):
        return None
    config = entry.get("config")
    return config if isinstance(config, Mapping) else entry


def _providers_node(entry: Any, authority_id: str, *, create: bool) -> Any:
    props = _bundle_props_node(entry)
    registry = props.get("authority_registry") if isinstance(props, Mapping) else None
    if not isinstance(registry, Mapping):
        if not create:
            return None
        props["authority_registry"] = {}
        registry = props["authority_registry"]
    authorities = registry.get("authorities")
    if not isinstance(authorities, Mapping):
        if not create:
            return None
        registry["authorities"] = {}
        authorities = registry["authorities"]
    authority = authorities.get(authority_id)
    if not isinstance(authority, Mapping):
        if not create:
            return None
        authorities[authority_id] = {}
        authority = authorities[authority_id]
    providers = authority.get("providers")
    if not isinstance(providers, Mapping):
        if not create:
            return None
        authority["providers"] = {}
        providers = authority["providers"]
    return providers


def _plain(value: Any) -> Any:
    """A plain-Python copy of a ruamel node, for validation and merging."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def edit_bundle_authority_provider(
    *,
    bundle_id: str,
    authority_id: str,
    provider_id: str,
    provider: Mapping[str, Any],
    allow_secret_removal: bool = False,
    create: bool = False,
    path: Path | None = None,
    policy_path: Path | None = None,
) -> DescriptorEdit:
    """Replace one provider block under `authority_registry` of one bundle."""
    target = path or bundles_path()
    bundle_id = str(bundle_id or "").strip() or DEFAULT_CONNECTION_HUB_BUNDLE
    authority_id = str(authority_id or "").strip()
    provider_id = str(provider_id or "").strip()
    if not authority_id or not provider_id:
        raise DescriptorEditRefused("target_missing", "Name the authority and the provider to edit.")
    effective_policy_path = policy_path or target.with_name("assembly.yaml")
    with descriptor_edit_lock(target):
        _guard(target, section="auth", policy_path=effective_policy_path)
        document = _load_rt(target)
        entry = _find_bundle_entry(document, bundle_id)
        if entry is None:
            raise DescriptorEditRefused("bundle_missing", f"No bundle `{bundle_id}` in {target.name}.")
        providers = _providers_node(entry, authority_id, create=create)
        if providers is None:
            raise DescriptorEditRefused("authority_missing", f"No authority `{authority_id}` with providers in bundle `{bundle_id}`.")
        existing = _plain(providers.get(provider_id)) if provider_id in providers else None
        if existing is None and not create:
            raise DescriptorEditRefused("provider_missing", f"No provider `{provider_id}` under authority `{authority_id}`.")
        merged = merge_unchanged(_plain(provider), existing)
        if existing is not None and not allow_secret_removal:
            dropped = dropped_secret_keys(merged, existing)
            if dropped:
                raise DescriptorEditRefused(
                    "secret_key_dropped",
                    "The submission drops keys that carry a secret reference or a cookie name; keep them, or say so.",
                    problems=[f"missing: {key}" for key in dropped],
                )
        problems = validate_authority_provider(merged)
        if problems:
            raise DescriptorEditRefused("invalid_provider", "The provider block does not resolve as submitted.", problems=problems)
        if existing == merged:
            return DescriptorEdit(path=str(target), backup="", changed=(), scope="providers", activation="none")
        providers[provider_id] = merged
        backup = _write_rt(target, document)
    return DescriptorEdit(
        path=str(target),
        backup=str(backup),
        changed=(f"{bundle_id}.authority_registry.authorities.{authority_id}.providers.{provider_id}",),
        scope="providers",
        activation="publish",
    )


def registry_provider_types(bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE, *, path: Path | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """`(authority_id, provider_id)` to `{type, enabled}` for every provider
    the bundles file declares, read from the file itself."""
    target = path or bundles_path()
    if not target.exists():
        return {}
    entry = _find_bundle_entry(_load_rt(target), str(bundle_id or "").strip() or DEFAULT_CONNECTION_HUB_BUNDLE)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    props = _bundle_props_node(entry)
    registry = props.get("authority_registry") if isinstance(props, Mapping) else None
    authorities = registry.get("authorities") if isinstance(registry, Mapping) else None
    for authority_id, authority in (authorities or {}).items() if isinstance(authorities, Mapping) else []:
        providers = authority.get("providers") if isinstance(authority, Mapping) else None
        for provider_id, provider in (providers or {}).items() if isinstance(providers, Mapping) else []:
            if isinstance(provider, Mapping):
                out[(str(authority_id), str(provider_id))] = {
                    "type": str(provider.get("type") or "").strip().lower(),
                    "enabled": provider.get("enabled", True) is not False,
                }
    return out


def edit_assembly_platform_sign_in(
    *,
    provider_id: str,
    authority_id: str = "",
    bundle_id: str = "",
    path: Path | None = None,
    bundles: Path | None = None,
    policy_path: Path | None = None,
) -> DescriptorEdit:
    """Point the platform at another app-defined sign-in provider.

    ``auth.type`` remains ``bundle`` because the definition is in the app;
    ``provider_id`` selects the concrete authenticator or login lane there.
    """
    target = path or assembly_path()
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        raise DescriptorEditRefused("target_missing", "Name the provider to sign in with.")
    with descriptor_edit_lock(target):
        _guard(target, section="auth", policy_path=policy_path or target)
        document = _load_rt(target)
        auth = document.get("auth") if isinstance(document, Mapping) else None
        if not isinstance(auth, Mapping):
            raise DescriptorEditRefused("auth_missing", f"No `auth` block in {target.name}.")
        hub = auth.get("connection_hub")
        if not isinstance(hub, Mapping):
            raise DescriptorEditRefused("auth_missing", "No `auth.connection_hub` block: this descriptor selects its authority another way.")
        bundle = str(bundle_id or hub.get("bundle_id") or DEFAULT_CONNECTION_HUB_BUNDLE).strip()
        authority = str(authority_id or hub.get("authority_id") or "kdcube.platform").strip()
        known = registry_provider_types(bundle, path=bundles)
        spec = known.get((authority, provider_id))
        if spec is None:
            raise DescriptorEditRefused("provider_unknown", f"Authority `{authority}` of bundle `{bundle}` declares no provider `{provider_id}`.")
        if not spec.get("enabled", True):
            raise DescriptorEditRefused("provider_disabled", f"Provider `{provider_id}` is disabled; enable it first.")
        auth_type = AUTH_TYPE_FOR_PROVIDER_TYPE.get(spec.get("type") or "")
        if not auth_type:
            raise DescriptorEditRefused("provider_type_unsupported", f"Provider `{provider_id}` has type `{spec.get('type')}`, which the platform cannot sign in with.")
        changed: list[str] = []
        if str(auth.get("type") or "") != auth_type:
            auth["type"] = auth_type
            changed.append("auth.type")
        if str(hub.get("provider_id") or "") != provider_id:
            hub["provider_id"] = provider_id
            changed.append("auth.connection_hub.provider_id")
        if not changed:
            return DescriptorEdit(path=str(target), backup="", changed=(), scope="lane", activation="none")
        backup = _write_rt(target, document)
    return DescriptorEdit(path=str(target), backup=str(backup), changed=tuple(changed), scope="lane", activation="refresh")


__all__ = [
    "AUTH_TYPE_FOR_PROVIDER_TYPE",
    "DescriptorEdit",
    "DescriptorEditRefused",
    "UNCHANGED",
    "assembly_path",
    "bundles_path",
    "dropped_secret_keys",
    "edit_assembly_platform_sign_in",
    "edit_bundle_authority_provider",
    "edits_enabled",
    "merge_unchanged",
    "platform_settings_editing_policy",
    "registry_provider_types",
    "validate_authority_provider",
]
