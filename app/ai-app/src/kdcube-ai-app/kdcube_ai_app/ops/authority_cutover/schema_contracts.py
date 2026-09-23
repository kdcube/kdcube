# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Column contracts for every table written by authority reset."""

from __future__ import annotations

from dataclasses import dataclass

from connection_hub.delegated_credentials.admission_replay import (
    TABLE_ADMISSION_REPLAY_CLAIMS,
)
from connection_hub.delegated_credentials.authority_cutover import (
    TABLE_AUTHORITY_CUTOVERS,
)
from connection_hub.delegated_credentials.cards.handle_schema import (
    TABLE_CARD_HANDLE_METADATA,
    TABLE_PREPARED_RESIDENT_SECRETS,
    TABLE_RETIRED_RESIDENT_SECRETS,
)
from connection_hub.delegated_credentials.oauth.authority_schema import (
    TABLE_ACCESS_BINDINGS,
    TABLE_CLIENTS,
    TABLE_FAMILIES,
    TABLE_REFRESH_GENERATIONS,
)
from kdcube_ai_app.auth.bundle.session_schema import (
    TABLE_SESSIONS,
    TABLE_USERS,
)
from kdcube_ai_app.auth.platform_session_schema import TABLE_PLATFORM_SESSIONS


@dataclass(frozen=True)
class TableColumnContract:
    table_name: str
    required_columns: frozenset[str]


def _contract(table_name: str, *columns: str) -> TableColumnContract:
    return TableColumnContract(
        table_name=table_name,
        required_columns=frozenset(columns),
    )


AUTHORITY_TARGET_TABLE_CONTRACTS = (
    _contract(
        TABLE_AUTHORITY_CUTOVERS,
        "generation_id",
        "activated_revision",
        "source_generation",
        "target_generation",
        "source_counts",
        "target_counts",
        "prerequisites",
        "preview_sha256",
        "applied_at",
    ),
    _contract(
        TABLE_CLIENTS,
        "client_id",
        "tenant",
        "project",
        "redirect_uris",
        "grant_types",
        "token_endpoint_auth_method",
        "application_type",
        "metadata",
        "revision",
        "created_at",
        "last_used_at",
        "expires_at",
        "retired_at",
    ),
    _contract(
        TABLE_FAMILIES,
        "family_id",
        "tenant",
        "project",
        "registry_access_id",
        "card_kind",
        "client_id",
        "subject",
        "identity_scope",
        "current_generation_id",
        "revision",
        "state",
        "created_at",
        "updated_at",
        "expires_at",
    ),
    _contract(
        TABLE_REFRESH_GENERATIONS,
        "generation_id",
        "family_id",
        "token_sha256",
        "record",
        "revision",
        "state",
        "created_at",
        "consumed_at",
        "revoked_at",
        "expires_at",
    ),
    _contract(
        TABLE_ACCESS_BINDINGS,
        "token_sha256",
        "tenant",
        "project",
        "registry_access_id",
        "record",
        "revision",
        "state",
        "created_at",
        "updated_at",
        "revoked_at",
        "expires_at",
    ),
    _contract(
        TABLE_CARD_HANDLE_METADATA,
        "access_id",
        "tenant",
        "project",
        "card_revision",
        "resident_access_secret_ref",
        "resident_access_sha256",
        "session_id",
        "state",
        "revision",
        "created_at",
        "updated_at",
        "retired_at",
        "expires_at",
        "cleanup_attempts",
        "cleanup_claim_token",
        "cleanup_claimed_at",
        "cleanup_claim_expires_at",
        "cleanup_next_attempt_at",
        "cleanup_last_error",
    ),
    _contract(
        TABLE_PREPARED_RESIDENT_SECRETS,
        "secret_ref",
        "access_id",
        "card_revision",
        "resident_access_sha256",
        "envelope_created_at",
        "expires_at",
        "prepared_at",
        "state",
        "cleanup_attempts",
        "cleanup_claim_token",
        "cleanup_claimed_at",
        "cleanup_claim_expires_at",
        "cleanup_next_attempt_at",
        "cleanup_last_error",
    ),
    _contract(
        TABLE_RETIRED_RESIDENT_SECRETS,
        "secret_ref",
        "access_id",
        "resident_access_sha256",
        "retired_at",
        "cleanup_attempts",
        "cleanup_claim_token",
        "cleanup_claimed_at",
        "cleanup_claim_expires_at",
        "cleanup_next_attempt_at",
        "cleanup_last_error",
    ),
    _contract(
        TABLE_ADMISSION_REPLAY_CLAIMS,
        "service_id",
        "nonce_sha256",
        "tenant",
        "project",
        "claimed_at",
        "expires_at",
    ),
    _contract(
        TABLE_USERS,
        "subject",
        "tenant",
        "project",
        "record",
        "session_version",
        "revision",
        "state",
        "disabled",
        "created_at",
        "updated_at",
    ),
    _contract(
        TABLE_SESSIONS,
        "session_id",
        "subject",
        "token_sha256",
        "record",
        "revision",
        "state",
        "issued_at",
        "idle_expires_at",
        "hard_expires_at",
        "last_seen_at",
        "revoked_at",
        "updated_at",
    ),
    _contract(
        TABLE_PLATFORM_SESSIONS,
        "session_id",
        "authority_key",
        "tenant",
        "project",
        "user_type",
        "user_id",
        "fingerprint",
        "record",
        "revision",
        "state",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
        "updated_at",
    ),
)


__all__ = [
    "AUTHORITY_TARGET_TABLE_CONTRACTS",
    "TableColumnContract",
]
