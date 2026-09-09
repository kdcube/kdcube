#!/usr/bin/env python3
"""Generate the shared application-site route matrix in proxy templates."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


AI_APP_ROOT = Path(__file__).resolve().parents[2]
ROUTE_PREFIX_TOKEN = "${ROUTE_PREFIX}"
SELECTOR_BEGIN = "# KDCUBE_APPLICATION_SITE_SELECTOR:BEGIN"
SELECTOR_END = "# KDCUBE_APPLICATION_SITE_SELECTOR:END"
ROUTES_BEGIN = "# KDCUBE_APPLICATION_SITE_ROUTES:BEGIN"
ROUTES_END = "# KDCUBE_APPLICATION_SITE_ROUTES:END"


@dataclass(frozen=True)
class RouteTarget:
    path: str
    selector_indent: str = "    "
    route_indent: str = "        "
    route_prefix: str = ROUTE_PREFIX_TOKEN


NGINX_TARGETS = (
    RouteTarget("deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"),
    RouteTarget("deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_delegated.conf"),
    RouteTarget("deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_ssl.conf"),
    RouteTarget(
        "deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_ssl_delegated_auth.conf"
    ),
    RouteTarget("deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy.conf"),
    RouteTarget(
        "deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_delegated.conf"
    ),
    RouteTarget("deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ecs.conf"),
    RouteTarget(
        "deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_cognito.conf"
    ),
    RouteTarget(
        "deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_delegated_auth.conf"
    ),
    RouteTarget(
        "deployment/docker/custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_hardcoded.conf"
    ),
    RouteTarget(
        "deployment/kubernetes/with-managed-infra/nginx/nginx_proxy_ssl_cognito.conf"
    ),
    RouteTarget(
        "deployment/kubernetes/with-managed-infra/nginx/nginx_proxy_ssl_delegated_auth.conf"
    ),
    RouteTarget(
        "deployment/kubernetes/with-managed-infra/nginx/nginx_proxy_ssl_hardcoded.conf"
    ),
)

HELM_TARGET = RouteTarget(
    "deployment/kubernetes/local/charts/kdcube-platform/templates/runtime-configmaps.yaml",
    selector_indent="    ",
    route_indent="      ",
    route_prefix="{{ $routePrefix }}",
)

TARGETS = (*NGINX_TARGETS, HELM_TARGET)


def _indented(lines: list[str], indent: str) -> str:
    return "\n".join(f"{indent}{line}" if line else "" for line in lines)


def selector_block(route_prefix: str, indent: str) -> str:
    """Return the HTTP-scope selector shared by every generated route matrix."""
    return _indented(
        [
            SELECTOR_BEGIN,
            f'map "{route_prefix}:$host" $kdcube_control_plane_at_root {{',
            "    ~^/:    1;",
            "    default 0;",
            "}",
            "",
            "map $uri $kdcube_control_plane_entry {",
            "    /       /chat;",
            f'    default "{route_prefix}/chat";',
            "}",
            SELECTOR_END,
        ],
        indent,
    )


def routes_block(route_prefix: str, forwarded_proto: str, indent: str) -> str:
    """Return the server-scope route matrix from one canonical definition."""
    lines = [
        ROUTES_BEGIN,
        "# Proxy-owned liveness is independent of application-site selection.",
        "location = /health {",
        "    default_type text/plain;",
        '    return 200 "ok\\n";',
        "}",
        "",
        "# The control plane owns only its descriptor-rendered mount.",
        f"location = {route_prefix} {{",
        "    absolute_redirect off;",
        "    return 302 $kdcube_control_plane_entry;",
        "}",
        "",
        f"location ^~ {route_prefix}/ {{",
        "    proxy_pass http://web_ui/;",
        "    proxy_set_header Host              $http_host;",
        "    proxy_set_header X-Forwarded-Host  $http_host;",
        "    proxy_set_header X-Real-IP         $remote_addr;",
        "    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
        f"    proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        '    add_header Cache-Control "no-store, no-cache, must-revalidate" always;',
        "}",
        "",
        "# Proc resolves aliases and host/default-selected application sites.",
        "location ^~ /sites/ {",
        "    rewrite ^/sites/(.*)$ /api/integrations/sites/$1 break;",
        "    proxy_pass http://chat_proc;",
        "    proxy_set_header Host              $http_host;",
        "    proxy_set_header X-Forwarded-Host  $http_host;",
        "    proxy_set_header X-Real-IP         $remote_addr;",
        "    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
        f"    proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        "}",
        "",
        "# The platform's own browser sign-in (server-held session): login,",
        "# callback and status live on the chat ingress beside /profile and",
        "# /api/platform/logout. Without this block the callback would fall to",
        "# the application-site fallback and the sign-in would end on a 200 HTML",
        "# page instead of a session cookie.",
        "location ^~ /api/platform/ {",
        "    proxy_pass http://chat_api;",
        "    proxy_set_header Host              $http_host;",
        "    proxy_set_header X-Forwarded-Host  $http_host;",
        "    proxy_set_header X-Real-IP         $remote_addr;",
        "    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
        f"    proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        '    add_header Cache-Control "no-store" always;',
        "    proxy_connect_timeout 30s;",
        "    proxy_send_timeout    30s;",
        "    proxy_read_timeout    30s;",
        "}",
        "",
        "# OAuth discovery (RFC 9728 protected-resource metadata, RFC 8414",
        "# authorization-server metadata). MCP clients probe these root paths",
        "# before or instead of following the WWW-Authenticate challenge, and the",
        "# application-site fallback below answers any unknown path with 200 HTML,",
        "# which a client then fails to parse as JSON. The pathless forms name no",
        "# resource or issuer on this deployment: exact locations answer them,",
        "# because a '/'-terminated prefix location would redirect the slashless",
        "# URI (a 301 to http:// for an https client). The path-inserted forms map",
        "# onto the bundle OAuth surface, which serves the same documents the",
        "# challenge points to. Any other /.well-known/ path is an honest 404; a",
        "# longer prefix such as ACME still wins over this one.",
        "location = /.well-known/oauth-protected-resource {",
        "    default_type application/json;",
        '    return 404 "{\\"detail\\":\\"Not Found\\"}";',
        "}",
        "",
        "location ^~ /.well-known/oauth-protected-resource/ {",
        '    rewrite "^/[.]well-known/oauth-protected-resource(/api/integrations/bundles/[^/]+/[^/]+/[^/]+/public)/(.+?)/?$" '
        f'"$1/oauth/.well-known/oauth-protected-resource?resource={forwarded_proto}://$http_host$1/$2" break;',
        "    proxy_pass http://chat_proc;",
        "    proxy_set_header Host              $http_host;",
        "    proxy_set_header X-Forwarded-Host  $http_host;",
        "    proxy_set_header X-Real-IP         $remote_addr;",
        "    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
        f"    proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        "}",
        "",
        "location = /.well-known/oauth-authorization-server {",
        "    default_type application/json;",
        '    return 404 "{\\"detail\\":\\"Not Found\\"}";',
        "}",
        "",
        "location ^~ /.well-known/oauth-authorization-server/ {",
        '    rewrite "^/[.]well-known/oauth-authorization-server(/api/integrations/bundles/[^/]+/[^/]+/[^/]+/public/oauth)/?$" '
        '"$1/.well-known/oauth-authorization-server" break;',
        "    proxy_pass http://chat_proc;",
        "    proxy_set_header Host              $http_host;",
        "    proxy_set_header X-Forwarded-Host  $http_host;",
        "    proxy_set_header X-Real-IP         $remote_addr;",
        "    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
        f"    proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        "}",
        "",
        "location ^~ /.well-known/ {",
        "    default_type application/json;",
        '    return 404 "{\\"detail\\":\\"Not Found\\"}";',
        "}",
        "",
        "location / {",
        "    # route_prefix '/' is valid only when no application site is enabled.",
        "    error_page 418 = @kdcube_control_plane_root;",
        "    if ($kdcube_control_plane_at_root = 1) { return 418; }",
        "",
        "    rewrite ^/$ /api/integrations/site-root break;",
        "    rewrite ^/(.*)$ /api/integrations/site-root/$1 break;",
        "    proxy_pass http://chat_proc;",
        "    proxy_set_header Host              $http_host;",
        "    proxy_set_header X-Forwarded-Host  $http_host;",
        "    proxy_set_header X-Real-IP         $remote_addr;",
        "    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
        f"    proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        "}",
        "",
        "location @kdcube_control_plane_root {",
        "    proxy_pass http://web_ui;",
        "    proxy_set_header Host              $http_host;",
        "    proxy_set_header X-Forwarded-Host  $http_host;",
        "    proxy_set_header X-Real-IP         $remote_addr;",
        "    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
        f"    proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        '    add_header Cache-Control "no-store, no-cache, must-revalidate" always;',
        "}",
        ROUTES_END,
    ]
    return _indented(lines, indent)


def _replace_marked_block(text: str, begin: str, end: str, replacement: str) -> str | None:
    pattern = re.compile(
        rf"^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}",
        re.MULTILINE | re.DOTALL,
    )
    if not pattern.search(text):
        return None
    return pattern.sub(lambda _match: replacement, text, count=1)


def _insert_selector(text: str, replacement: str) -> str:
    updated = _replace_marked_block(text, SELECTOR_BEGIN, SELECTOR_END, replacement)
    if updated is not None:
        return updated
    raise ValueError(f"generated selector markers missing: {SELECTOR_BEGIN}")


def _replace_routes(text: str, replacement: str) -> str:
    updated = _replace_marked_block(text, ROUTES_BEGIN, ROUTES_END, replacement)
    if updated is not None:
        return updated
    raise ValueError(f"generated route markers missing: {ROUTES_BEGIN}")


def render_target(text: str, target: RouteTarget) -> str:
    forwarded_proto = "$forwarded_proto" if "$forwarded_proto" in text else "$scheme"
    text = _insert_selector(
        text,
        selector_block(target.route_prefix, target.selector_indent),
    )
    text = _replace_routes(
        text,
        routes_block(target.route_prefix, forwarded_proto, target.route_indent),
    )
    return text


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write generated blocks")
    mode.add_argument("--check", action="store_true", help="fail when generated blocks drift")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    drifted: list[str] = []
    for target in TARGETS:
        path = AI_APP_ROOT / target.path
        current = path.read_text(encoding="utf-8")
        rendered = render_target(current, target)
        if rendered == current:
            continue
        if args.write:
            path.write_text(rendered, encoding="utf-8")
        else:
            drifted.append(target.path)

    if drifted:
        for path in drifted:
            print(f"generated application-site routes differ: {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
