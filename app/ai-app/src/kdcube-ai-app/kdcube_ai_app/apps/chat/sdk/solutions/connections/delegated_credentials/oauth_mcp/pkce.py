# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""PKCE (RFC 7636) S256 challenge helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac


def make_s256_challenge(code_verifier: str) -> str:
    """BASE64URL(SHA256(ASCII(code_verifier))) without ``=`` padding."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_s256(code_verifier: str, code_challenge: str) -> bool:
    """Constant-time check that ``code_verifier`` matches the stored challenge."""
    expected = make_s256_challenge(code_verifier)
    return hmac.compare_digest(expected, code_challenge)
