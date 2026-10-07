# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Canonical Hub begin arguments from the hosting app's validated consent data.

These defaults mirror the public begin_oauth_issuance contract. They bind the
same digest at both sides; selecting grants and checking live authority remain
host and Hub responsibilities. No client-selected provider or original id is
accepted here.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    OriginalExchangeRefused, canonical, text,
)

_DEFAULTS = {
    "client_label": "", "scopes": (), "operations": None,
    "resource_grants": None, "resource_operations": None, "resource": "",
    "access_id": "", "card_kind": "", "identity_scope": "", "account_scope": None,
    "named_service_operations": None, "catalog_version": "", "client_metadata": None,
    "properties": None, "replace_authority": True, "expected_card_revision": None,
}


def oauth_issuance_arguments(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze every public Hub argument before capturing its original digest."""
    allowed = set(_DEFAULTS) | {"grantor_subject", "client_id", "invocation_policies"}
    if not isinstance(inputs, Mapping) or not set(inputs) <= allowed:
        raise OriginalExchangeRefused("original_exchange_binding_invalid")
    values = {**_DEFAULTS, **inputs}
    # Hub deliberately omits this optional key from the original digest when
    # None. An explicit empty selection is different and stays in the binding.
    if values.get("invocation_policies") is None:
        values.pop("invocation_policies", None)
    elif not isinstance(values["invocation_policies"], Mapping):
        raise OriginalExchangeRefused("original_exchange_binding_invalid")
    for name in ("grantor_subject", "client_id"):
        text(values.get(name))
    if type(values["replace_authority"]) is not bool:
        raise OriginalExchangeRefused("original_exchange_binding_invalid")
    for name in ("scopes", "operations"):
        value = values[name]
        if name == "operations" and value is None:
            continue
        if type(value) not in {list, tuple}:
            raise OriginalExchangeRefused("original_exchange_binding_invalid")
        values[name] = list(value)
    return json.loads(canonical(values))


__all__ = ["oauth_issuance_arguments"]
