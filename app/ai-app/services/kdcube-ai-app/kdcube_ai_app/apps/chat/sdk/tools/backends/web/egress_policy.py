import ipaddress
import socket
import asyncio
import hashlib
from typing import Optional, Union, Dict, Any, Iterable
from enum import Enum
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse, urlunparse

from kdcube_ai_app.apps.chat.sdk.tools.backends.web.reputation.abstract_reputation_provider import \
    AbstractReputationProvider
from kdcube_ai_app.infra.service_hub.cache import KVCache, create_kv_cache


class ReasonCode(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED_LOCALHOST = "BLOCKED_LOCALHOST"
    BLOCKED_PRIVATE_IP = "BLOCKED_PRIVATE_IP"
    BLOCKED_LINK_LOCAL = "BLOCKED_LINK_LOCAL"
    BLOCKED_UNSAFE_REPUTATION = "BLOCKED_UNSAFE_REPUTATION"
    BLOCKED_NOT_IN_ALLOWLIST = "BLOCKED_NOT_IN_ALLOWLIST"
    RESOLUTION_FAILED = "RESOLUTION_FAILED"
    INVALID_URL = "INVALID_URL"


@dataclass
class Verdict:
    allowed: bool
    reason: ReasonCode
    details: Dict[str, Any] = field(default_factory=dict)

BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}

def normalize_hostname(hostname: str) -> str:
    if not hostname:
        return ""
    normalized = hostname.strip().lower()
    if normalized.endswith("."):
        normalized = normalized[:-1]

    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]

    return normalized


def sanitize_url_for_scanning(url: str) -> str:
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", ""))
    except Exception:
        return url


def _get_ip_violation_reason(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> Optional[ReasonCode]:
    if ip.is_loopback:
        return ReasonCode.BLOCKED_LOCALHOST
    if ip.is_link_local:
        return ReasonCode.BLOCKED_LINK_LOCAL

    if isinstance(ip, ipaddress.IPv6Address):
         if ip.is_private:
             return ReasonCode.BLOCKED_PRIVATE_IP
         if str(ip).startswith("fec0:"):
             return ReasonCode.BLOCKED_PRIVATE_IP
         if ip == ipaddress.IPv6Address("::"):
             return ReasonCode.BLOCKED_PRIVATE_IP

    if isinstance(ip, ipaddress.IPv4Address):
        if str(ip).startswith("0."):
            return ReasonCode.BLOCKED_PRIVATE_IP
        if ip.is_private:
            return ReasonCode.BLOCKED_PRIVATE_IP
        if ip in ipaddress.IPv4Network("100.64.0.0/10"):
            return ReasonCode.BLOCKED_PRIVATE_IP
        if ip.is_multicast or ip.is_reserved:
            return ReasonCode.BLOCKED_PRIVATE_IP

    return None


def is_blocked_hostname(hostname: str, extra_blocked: Optional[Iterable[str]] = None) -> bool:
    normalized = normalize_hostname(hostname)
    if not normalized:
        return False

    if normalized in BLOCKED_HOSTNAMES:
        return True

    if extra_blocked:
        for extra in extra_blocked:
            if normalize_hostname(extra) == normalized:
                return True

    if (normalized.endswith(".localhost") or
            normalized.endswith(".local") or
            normalized.endswith(".internal")):
        return True

    return False


class NavigationGuard:
    def __init__(
        self,
        reputation_provider: Optional[AbstractReputationProvider] = None,
        cache: Optional[KVCache] = None
    ):
        self.reputation_provider = reputation_provider
        self.cache = cache or create_kv_cache()

    def _generate_cache_key(self, hostname: str) -> str:
        normalized = normalize_hostname(hostname)
        hash_digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        return f"nav_guard_egress:{hash_digest}"

    async def get_navigation_verdict(self, url: str, context: Optional[Dict[str, Any]] = None) -> Verdict:
        """
        Analyzes a URL and returns a Verdict indicating if it is safe to navigate to.

        Args:
            url: The full URL or hostname to check.
            context: Optional dictionary for configuration (e.g., 'allowlist', 'check_reputation').
                     Structure example: {'allowlist': ['example.com'], 'check_reputation': True}

        Returns:
            Verdict object containing allowed status, reason code, and details (resolved IP, hostname).
        """
        context = context or {}

        if "://" not in url:
            hostname = url
        else:
            try:
                parsed = urlparse(url)
                hostname = parsed.hostname
                if not hostname:
                    if parsed.path and not parsed.netloc:
                        return Verdict(False, ReasonCode.INVALID_URL, {"error": "Local file or invalid scheme"})
                    return Verdict(False, ReasonCode.INVALID_URL, {"error": "No hostname found"})
            except Exception as e:
                return Verdict(False, ReasonCode.INVALID_URL, {"error": str(e)})

        normalized_host = normalize_hostname(hostname)

        allowlist = context.get('allowlist', [])
        if allowlist:
            if normalized_host not in [normalize_hostname(h) for h in allowlist]:
                return Verdict(False, ReasonCode.BLOCKED_NOT_IN_ALLOWLIST, {"hostname": normalized_host})

        extra_blocked = context.get('blocked_hostnames', [])
        if is_blocked_hostname(normalized_host, extra_blocked):
            return Verdict(False, ReasonCode.BLOCKED_LOCALHOST, {"hostname": normalized_host})

        cache_key = self._generate_cache_key(normalized_host)

        cached = await self.cache.get_json(cache_key) if self.cache else None
        if cached:
            return Verdict(**cached)

        try:
            try:
                ip_obj = ipaddress.ip_address(normalized_host)
                violation = _get_ip_violation_reason(ip_obj)
                if violation:
                    return Verdict(False, violation, {"ip": str(ip_obj), "input_type": "ip_literal"})
                
                final_details = {"ip": str(ip_obj), "input_type": "ip_literal"}
            except ValueError:
                loop = asyncio.get_running_loop()
                addr_info = await loop.getaddrinfo(normalized_host, None, proto=socket.IPPROTO_TCP)

                resolved_ips = set()
                for _, _, _, _, sockaddr in addr_info:
                    resolved_ips.add(sockaddr[0])

                if not resolved_ips:
                    return Verdict(False, ReasonCode.RESOLUTION_FAILED, {"hostname": normalized_host})

                for ip_str in resolved_ips:
                    try:
                        ip_obj = ipaddress.ip_address(ip_str)
                        violation = _get_ip_violation_reason(ip_obj)
                        if violation:
                            return Verdict(False, violation, {"hostname": normalized_host, "blocked_ip": ip_str})
                    except ValueError:
                        continue
                
                final_details = {"hostname": normalized_host, "resolved_ips": list(resolved_ips)}

        except Exception as e:
            return Verdict(False, ReasonCode.RESOLUTION_FAILED, {"hostname": normalized_host, "error": str(e)})

        if context.get('check_reputation'):
            url_to_scan = sanitize_url_for_scanning(url)
            is_safe = await self.reputation_provider.check_url(url_to_scan)
            if not is_safe:
                verdict = Verdict(False, ReasonCode.BLOCKED_UNSAFE_REPUTATION, {
                    "hostname": normalized_host,
                    "provider": "google_safe_browsing"
                })
                await self._save_to_cache(cache_key, verdict)

                return verdict

        verdict = Verdict(True, ReasonCode.ALLOWED, final_details)
        await self._save_to_cache(cache_key, verdict)

        return verdict

    async def _save_to_cache(self, key: str, verdict: Verdict):
        if not self.cache:
            return
        data = asdict(verdict)
        data['reason'] = verdict.reason.value
        await self.cache.set_json(key, data)