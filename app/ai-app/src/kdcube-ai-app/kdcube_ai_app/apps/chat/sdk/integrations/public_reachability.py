"""Anonymous reachability of deployment-owned URLs handed to providers.

Callers choose the expected response and the kind of public fetcher. This
module probes an origin already selected by trusted deployment configuration;
foreign image URLs retain their consumer's SSRF and content validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

import httpx


PublicCaller = Literal["browser", "server"]
PublicReachability = Literal["reachable", "answered_by_other", "unreachable"]
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
)
MAX_MARKER_BYTES = 16 * 1024


def public_fetch_headers(caller: PublicCaller) -> dict[str, str]:
    """Headers for an anonymous caller, shared with provider-file downloads."""
    if caller == "browser":
        return {"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"}
    if caller == "server":
        return {"User-Agent": "KDCube-Public-Reachability/1.0", "Accept": "*/*"}
    raise ValueError("Public caller must be browser or server.")


@dataclass(frozen=True)
class PublicURLExpectation:
    status: int = 200
    media_prefix: str = ""
    # A marker, when needed, must occur in the first bounded response bytes.
    marker: bytes | None = None


@dataclass(frozen=True)
class PublicURLResult:
    kind: PublicReachability
    status: int | None = None
    media_type: str = ""
    detail: str = ""


async def probe_public_url(
    url: str, *, caller: PublicCaller, expect: PublicURLExpectation
) -> PublicURLResult:
    """Check status, media type and optional marker without retaining the URL.

    Each probe uses a fresh unauthenticated client. Status/type checks consume
    no body; marker checks read at most 16 KiB. Exception text can contain a
    signed capability URL, so only its class is retained in the result.
    """
    headers = public_fetch_headers(caller)
    if expect.marker is not None and len(expect.marker) > MAX_MARKER_BYTES:
        raise ValueError("Public response marker exceeds the probe byte limit.")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            async with client.stream(
                "GET", url, headers=headers, follow_redirects=True
            ) as response:
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                status = response.status_code
                if status != expect.status or not media_type.startswith(expect.media_prefix.lower()):
                    return PublicURLResult("answered_by_other", status, media_type)
                if expect.marker:
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        body.extend(chunk[: MAX_MARKER_BYTES - len(body)])
                        if expect.marker in body:
                            break
                        if len(body) >= MAX_MARKER_BYTES:
                            return PublicURLResult("answered_by_other", status, media_type)
                    if expect.marker not in body:
                        return PublicURLResult("answered_by_other", status, media_type)
                return PublicURLResult("reachable", status, media_type)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return PublicURLResult("unreachable", detail=type(exc).__name__)


class DeploymentOriginError(RuntimeError):
    """A provider cannot fetch a file from the configured public origin."""

    status = 502

    def __init__(self, result: PublicURLResult, *, purpose: str):
        if result.kind == "reachable":
            raise ValueError("A reachable origin has no deployment refusal.")
        self.code = (
            "deployment_origin_unreachable"
            if result.kind == "unreachable" else "deployment_origin_not_public"
        )
        self.details: dict[str, Any] = {"where": "deployment_config", "purpose": purpose}
        if result.kind == "unreachable":
            self.details["error"] = result.detail
            reason = "did not answer an anonymous browser-like fetch of the staged file"
        else:
            self.details.update(origin_status=result.status, origin_media_type=result.media_type)
            reason = (
                "answered an anonymous browser-like fetch with "
                f"{result.media_type or 'no content type'} (HTTP {result.status}) "
                "instead of the expected file"
            )
        super().__init__(
            f"This deployment's public origin {reason}. Nothing was written. "
            "Ask the runtime administrator to fix the configured public origin, "
            "tunnel or proxy so it serves this file to an anonymous caller."
        )

    def as_response(self, request: Any, *, provider: Mapping[str, Any]):
        from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceResponse

        return NamedServiceResponse.error_response(
            code=self.code, message=str(self), status=self.status, details=self.details,
            provider=dict(provider), namespace=request.namespace, object_ref=request.object_ref,
        )


def deployment_origin_error(
    result: PublicURLResult, *, purpose: str
) -> DeploymentOriginError | None:
    return None if result.kind == "reachable" else DeploymentOriginError(result, purpose=purpose)
