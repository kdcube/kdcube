# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Bundle-owned platform session auth helpers."""

from .sessions import (
    BUNDLE_SESSION_SECRET_KEY,
    SESSION_TOKEN_PREFIX,
    SESSION_TOKEN_SCHEMA,
    BundleSessionAuthManager,
    SessionOrTokenAuthManager,
    BundleSessionError,
    BundleSessionExpired,
    BundleSessionGrant,
    BundleSessionInvalid,
    BundleSessionUser,
    BundleSessionVerification,
    BundleSessionAuthority,
    delete_bundle_session_user,
    get_bundle_session_authority,
    invalidate_bundle_session_user,
    login_bundle_session,
    login_or_register_bundle_session,
    logout_bundle_session,
    register_bundle_session_user,
    validate_bundle_session_token,
)

__all__ = [
    "BUNDLE_SESSION_SECRET_KEY",
    "SESSION_TOKEN_PREFIX",
    "SESSION_TOKEN_SCHEMA",
    "BundleSessionAuthManager",
    "SessionOrTokenAuthManager",
    "BundleSessionAuthority",
    "BundleSessionError",
    "BundleSessionExpired",
    "BundleSessionGrant",
    "BundleSessionInvalid",
    "BundleSessionUser",
    "BundleSessionVerification",
    "delete_bundle_session_user",
    "get_bundle_session_authority",
    "invalidate_bundle_session_user",
    "login_bundle_session",
    "login_or_register_bundle_session",
    "logout_bundle_session",
    "register_bundle_session_user",
    "validate_bundle_session_token",
]
