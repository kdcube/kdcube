# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""OAuth Client ID Metadata Document resolution.

The ``client_id`` is attacker-controlled at this point in the OAuth flow. The
resolver therefore treats metadata retrieval as an SSRF-sensitive operation:
it resolves and validates every address before connecting, pins the approved
addresses into the HTTP connector, sends no ambient credentials, follows no
redirects, and bounds both transfer and decoded JSON size.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
import zlib
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

import aiohttp

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.clients import (
    CLIENT_REGISTRATION_METADATA_DOCUMENT,
    PublicClient,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    OAuthDelegatedClientMetadataDocumentsConfig,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    GrantStore,
)

MAX_CLIENT_ID_CHARS = 2048
MAX_REDIRECT_URIS = 32
MAX_CLIENT_NAME_CHARS = 200
_LOOPBACK_REDIRECT_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ClientMetadataError(Exception):
    def __init__(
        self,
        code: str,
        description: str,
        *,
        status_code: int = 400,
    ):
        super().__init__(description)
        self.code = code
        self.description = description
        self.status_code = status_code


@dataclass(frozen=True)
class ClientMetadataFetch:
    document: Mapping[str, Any]
    cache_ttl_seconds: int | None
    cacheable: bool = True


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, host: str, addresses: Sequence[str]):
        self._host = host
        self._addresses = tuple(addresses)

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        if host.lower().rstrip(".") != self._host:
            raise OSError("unexpected metadata host")
        rows: list[dict[str, Any]] = []
        for address in self._addresses:
            ip = ipaddress.ip_address(address)
            address_family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            if family not in {socket.AF_UNSPEC, address_family}:
                continue
            rows.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": port,
                    "family": address_family,
                    "proto": socket.IPPROTO_TCP,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not rows:
            raise OSError("metadata host has no approved address for this socket family")
        return rows

    async def close(self) -> None:
        return None


def is_client_metadata_id(client_id: str) -> bool:
    try:
        parsed = urlsplit(str(client_id or ""))
    except Exception:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def validate_client_metadata_url(
    client_id: str,
    config: OAuthDelegatedClientMetadataDocumentsConfig,
) -> tuple[str, int]:
    if not client_id or len(client_id) > MAX_CLIENT_ID_CHARS:
        raise ClientMetadataError("invalid_client", "client metadata URL is invalid")
    try:
        parsed = urlsplit(client_id)
        port = parsed.port or 443
    except (TypeError, ValueError):
        raise ClientMetadataError("invalid_client", "client metadata URL is invalid") from None
    host = str(parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path
    ):
        raise ClientMetadataError(
            "invalid_client",
            "client_id must be an HTTPS metadata URL with a path",
        )
    if any(
        unquote(segment) in {".", ".."}
        for segment in parsed.path.split("/")
    ):
        raise ClientMetadataError(
            "invalid_client",
            "client metadata URL cannot contain dot path segments",
        )
    allowed = tuple(domain.lower().rstrip(".") for domain in config.allowed_domains)
    if allowed and not any(
        host == domain or (config.allow_subdomains and host.endswith(f".{domain}"))
        for domain in allowed
    ):
        raise ClientMetadataError(
            "unauthorized_client",
            "client metadata domain is not allowed by this deployment",
        )
    return host, port


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


async def resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    try:
        rows = await loop.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise ClientMetadataError(
            "temporarily_unavailable",
            "client metadata host could not be resolved",
            status_code=503,
        ) from exc
    addresses = tuple(dict.fromkeys(str(row[4][0]) for row in rows))
    if not addresses or not all(_public_address(address) for address in addresses):
        raise ClientMetadataError(
            "invalid_client",
            "client metadata host resolves to a non-public address",
        )
    return addresses


def _cache_ttl(
    headers: Mapping[str, str],
    config: OAuthDelegatedClientMetadataDocumentsConfig,
) -> tuple[int | None, bool]:
    directives: dict[str, str | None] = {}
    for token in str(headers.get("Cache-Control") or "").split(","):
        name, separator, value = token.strip().partition("=")
        if name:
            directives[name.lower()] = value.strip().strip('"') if separator else None
    # ``no-cache`` allows storage only with revalidation. KDCube's shared
    # cache stores complete snapshots, so treating it as non-storable is the
    # conservative way to honor that directive without conditional requests.
    if {"no-store", "no-cache", "private"}.intersection(directives):
        return None, False
    ttl = config.cache_ttl_seconds
    if "max-age" in directives:
        try:
            ttl = max(0, int(str(directives["max-age"])))
        except (TypeError, ValueError):
            ttl = config.cache_ttl_seconds
    elif headers.get("Expires"):
        try:
            expires = parsedate_to_datetime(str(headers["Expires"]))
            ttl = max(0, int(expires.timestamp() - time.time()))
        except Exception:
            ttl = config.cache_ttl_seconds
    return min(ttl, config.cache_max_ttl_seconds), True


def _decode_body(raw: bytes, encoding: str, max_bytes: int) -> bytes:
    normalized = encoding.strip().lower()
    if normalized in {"", "identity"}:
        decoded = raw
    elif normalized == "gzip":
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            decoded = decoder.decompress(raw, max_bytes + 1)
            if len(decoded) <= max_bytes:
                decoded += decoder.flush(max_bytes + 1 - len(decoded))
        except zlib.error as exc:
            raise ClientMetadataError("invalid_client_metadata", "client metadata gzip is invalid") from exc
    else:
        raise ClientMetadataError(
            "invalid_client_metadata",
            "client metadata uses an unsupported content encoding",
        )
    if len(decoded) > max_bytes:
        raise ClientMetadataError("invalid_client_metadata", "client metadata document is too large")
    return decoded


async def fetch_client_metadata_document(
    client_id: str,
    config: OAuthDelegatedClientMetadataDocumentsConfig,
    *,
    address_resolver: Callable[[str, int], Awaitable[Sequence[str]]] | None = None,
) -> ClientMetadataFetch:
    host, port = validate_client_metadata_url(client_id, config)
    resolver = address_resolver or resolve_public_addresses
    try:
        async with asyncio.timeout(config.fetch_timeout_seconds):
            addresses = tuple(await resolver(host, port))
    except TimeoutError as exc:
        raise ClientMetadataError(
            "temporarily_unavailable",
            "client metadata host resolution timed out",
            status_code=503,
        ) from exc
    if not addresses or not all(_public_address(address) for address in addresses):
        raise ClientMetadataError(
            "invalid_client",
            "client metadata host resolves to a non-public address",
        )

    connector = aiohttp.TCPConnector(
        resolver=_PinnedResolver(host, addresses),
        ttl_dns_cache=0,
        use_dns_cache=False,
        limit=1,
    )
    timeout = aiohttp.ClientTimeout(total=config.fetch_timeout_seconds)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            headers={
                "Accept": "application/json, application/*+json",
                "Accept-Encoding": "identity, gzip",
                "User-Agent": "KDCube-OAuth-CIMD/1",
            },
        ) as session:
            async with session.get(client_id, allow_redirects=False) as response:
                if 300 <= response.status < 400:
                    raise ClientMetadataError(
                        "invalid_client",
                        "client metadata redirects are not accepted",
                    )
                if response.status != 200:
                    raise ClientMetadataError(
                        "invalid_client",
                        f"client metadata endpoint returned HTTP {response.status}",
                    )
                media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if media_type != "application/json" and not media_type.endswith("+json"):
                    raise ClientMetadataError(
                        "invalid_client_metadata",
                        "client metadata response must be JSON",
                    )
                content_length = response.content_length
                if content_length is not None and content_length > config.max_document_bytes:
                    raise ClientMetadataError(
                        "invalid_client_metadata",
                        "client metadata document is too large",
                    )
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > config.max_document_bytes:
                        raise ClientMetadataError(
                            "invalid_client_metadata",
                            "client metadata transfer is too large",
                        )
                decoded = _decode_body(
                    bytes(raw),
                    str(response.headers.get("Content-Encoding") or ""),
                    config.max_document_bytes,
                )
                try:
                    document = json.loads(decoded.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ClientMetadataError(
                        "invalid_client_metadata",
                        "client metadata document is not valid UTF-8 JSON",
                    ) from exc
                if not isinstance(document, dict):
                    raise ClientMetadataError(
                        "invalid_client_metadata",
                        "client metadata document must be a JSON object",
                    )
                ttl, cacheable = _cache_ttl(response.headers, config)
                return ClientMetadataFetch(document=document, cache_ttl_seconds=ttl, cacheable=cacheable)
    except asyncio.TimeoutError as exc:
        raise ClientMetadataError(
            "temporarily_unavailable",
            "client metadata request timed out",
            status_code=503,
        ) from exc
    except aiohttp.ClientError as exc:
        raise ClientMetadataError(
            "temporarily_unavailable",
            "client metadata request failed",
            status_code=503,
        ) from exc


def _metadata_text(
    document: Mapping[str, Any],
    name: str,
    *,
    required: bool = False,
    max_chars: int = MAX_CLIENT_ID_CHARS,
) -> str:
    value = document.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ClientMetadataError(
            "invalid_client_metadata",
            f"client metadata {name} must be a string",
        )
    text = value.strip()
    if (required and not text) or len(text) > max_chars:
        raise ClientMetadataError(
            "invalid_client_metadata",
            f"client metadata {name} is invalid",
        )
    return text


def _valid_redirect_uri(uri: str, application_type: str) -> bool:
    if uri != uri.strip() or any(ord(char) < 0x20 for char in uri):
        return False
    try:
        parsed = urlsplit(uri)
        _port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        not parsed.scheme
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    if parsed.scheme.lower() == "https":
        return bool(parsed.hostname)
    return bool(
        application_type == "native"
        and parsed.scheme.lower() == "http"
        and parsed.hostname in _LOOPBACK_REDIRECT_HOSTS
    )


def _all_http_loopback(redirects: Any) -> bool:
    """Every entry is an http URI on a loopback host (and there is at least one)."""
    if not isinstance(redirects, list) or not redirects:
        return False
    for uri in redirects:
        if not isinstance(uri, str):
            return False
        try:
            parsed = urlsplit(uri)
            _port = parsed.port
        except (TypeError, ValueError):
            return False
        if parsed.scheme.lower() != "http" or parsed.hostname not in _LOOPBACK_REDIRECT_HOSTS:
            return False
    return True


def validate_client_metadata_document(client_id: str, document: Mapping[str, Any]) -> PublicClient:
    if not isinstance(document.get("client_id"), str) or document["client_id"] != client_id:
        raise ClientMetadataError(
            "invalid_client_metadata",
            "client metadata client_id does not match its document URL",
        )
    client_name = _metadata_text(
        document,
        "client_name",
        required=True,
        max_chars=MAX_CLIENT_NAME_CHARS,
    )
    if "client_secret" in document or "client_secret_expires_at" in document:
        raise ClientMetadataError(
            "invalid_client_metadata",
            "client metadata documents cannot contain shared client secrets",
        )
    application_type = _metadata_text(document, "application_type").lower()
    if not application_type:
        # Absent means "web" in OIDC registration semantics, and a web client
        # may not use a plain-http redirect. A document whose redirects are ALL
        # http loopback describes a native client; reading it as web rejects it
        # outright (Claude Code publishes no application_type). Any non-loopback
        # http redirect still lands on "web" and is refused below.
        application_type = "native" if _all_http_loopback(document.get("redirect_uris")) else "web"
    if application_type not in {"native", "web"}:
        raise ClientMetadataError(
            "invalid_client_metadata",
            "client metadata application_type must be 'native' or 'web'",
        )
    redirects = document.get("redirect_uris")
    if (
        not isinstance(redirects, list)
        or not redirects
        or len(redirects) > MAX_REDIRECT_URIS
        or not all(isinstance(uri, str) and uri and len(uri) <= MAX_CLIENT_ID_CHARS for uri in redirects)
        or len(set(redirects)) != len(redirects)
        or not all(_valid_redirect_uri(uri, application_type) for uri in redirects)
    ):
        raise ClientMetadataError(
            "invalid_client_metadata",
            "client metadata requires valid redirect_uris",
        )
    token_auth_method = _metadata_text(document, "token_endpoint_auth_method") or "none"
    if token_auth_method != "none":
        raise ClientMetadataError(
            "unauthorized_client",
            "this deployment supports public metadata-document clients only",
        )
    grant_types = document.get("grant_types")
    if grant_types is not None and (
        not isinstance(grant_types, list)
        or not all(isinstance(item, str) for item in grant_types)
        or "authorization_code" not in grant_types
    ):
        raise ClientMetadataError(
            "unauthorized_client",
            "client metadata must support authorization_code",
        )
    response_types = document.get("response_types")
    if response_types is not None and (
        not isinstance(response_types, list)
        or not all(isinstance(item, str) for item in response_types)
        or "code" not in response_types
    ):
        raise ClientMetadataError(
            "unauthorized_client",
            "client metadata must support response type code",
        )
    return PublicClient(
        client_id=client_id,
        redirect_uris=tuple(redirects),
        token_endpoint_auth_method=token_auth_method,
        application_type=application_type,
        registration_kind=CLIENT_REGISTRATION_METADATA_DOCUMENT,
        client_name=client_name,
        client_uri=_metadata_text(document, "client_uri"),
        logo_uri=_metadata_text(document, "logo_uri"),
    )


async def resolve_client_metadata_document(
    client_id: str,
    *,
    config: OAuthDelegatedClientMetadataDocumentsConfig,
    store: GrantStore,
    fetcher: Callable[
        [str, OAuthDelegatedClientMetadataDocumentsConfig],
        Awaitable[ClientMetadataFetch],
    ]
    | None = None,
) -> PublicClient:
    validate_client_metadata_url(client_id, config)
    cached = await store.get_client_metadata_cache(client_id)
    if cached is not None:
        if cached.get("status") == "ok" and isinstance(cached.get("client"), dict):
            try:
                return validate_client_metadata_document(client_id, cached["client"])
            except ClientMetadataError:
                # Invalid documents must not remain cached. Refetch once so a
                # corrupt/stale shared-cache entry cannot become a durable
                # client denial.
                await store.delete_client_metadata_cache(client_id)
        else:
            # KDCube stores positive snapshots only. Remove any stale record
            # from an older rollout rather than honoring a negative cache.
            await store.delete_client_metadata_cache(client_id)

    try:
        fetched = (
            await fetcher(client_id, config)
            if fetcher is not None
            else await fetch_client_metadata_document(client_id, config)
        )
        client = validate_client_metadata_document(client_id, fetched.document)
    except ClientMetadataError:
        # The CIMD draft forbids caching fetch errors and malformed documents.
        raise

    if fetched.cacheable and fetched.cache_ttl_seconds:
        await store.cache_client_metadata_document(
            client_id,
            client.snapshot(),
            ttl_seconds=min(fetched.cache_ttl_seconds, config.cache_max_ttl_seconds),
        )
    return client
