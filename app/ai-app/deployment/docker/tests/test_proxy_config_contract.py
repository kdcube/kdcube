from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import yaml


DOCKER_ROOT = Path(__file__).resolve().parents[1]
AI_APP_ROOT = DOCKER_ROOT.parents[1]
REPOSITORY_ROOT = AI_APP_ROOT.parents[1]
ROUTE_GENERATOR = AI_APP_ROOT / "deployment/nginx/generate_application_site_routes.py"

PROXY_ROUTE_TEMPLATES = (
    DOCKER_ROOT / "all_in_one_kdcube/nginx/conf/nginx_proxy.conf",
    DOCKER_ROOT / "all_in_one_kdcube/nginx/conf/nginx_proxy_delegated.conf",
    DOCKER_ROOT / "all_in_one_kdcube/nginx/conf/nginx_proxy_ssl.conf",
    DOCKER_ROOT / "all_in_one_kdcube/nginx/conf/nginx_proxy_ssl_delegated_auth.conf",
    DOCKER_ROOT / "custom-ui-managed-infra/nginx/conf/nginx_proxy.conf",
    DOCKER_ROOT / "custom-ui-managed-infra/nginx/conf/nginx_proxy_delegated.conf",
    DOCKER_ROOT / "custom-ui-managed-infra/nginx/conf/nginx_proxy_ecs.conf",
    DOCKER_ROOT / "custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_cognito.conf",
    DOCKER_ROOT / "custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_delegated_auth.conf",
    DOCKER_ROOT / "custom-ui-managed-infra/nginx/conf/nginx_proxy_ssl_hardcoded.conf",
    AI_APP_ROOT / "deployment/kubernetes/with-managed-infra/nginx/nginx_proxy_ssl_cognito.conf",
    AI_APP_ROOT
    / "deployment/kubernetes/with-managed-infra/nginx/nginx_proxy_ssl_delegated_auth.conf",
    AI_APP_ROOT / "deployment/kubernetes/with-managed-infra/nginx/nginx_proxy_ssl_hardcoded.conf",
)


class ProxyConfigContractTest(unittest.TestCase):
    def test_proxylogin_is_an_explicit_compose_profile(self) -> None:
        compose_paths = (
            DOCKER_ROOT / "all_in_one_kdcube/docker-compose.yaml",
            DOCKER_ROOT / "custom-ui-managed-infra/docker-compose.yaml",
            DOCKER_ROOT / "local-infra-stack/docker-compose.yaml",
        )

        for compose_path in compose_paths:
            with self.subTest(compose=compose_path):
                compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    compose["services"]["proxylogin"]["profiles"],
                    ["proxylogin"],
                )

        for compose_path in compose_paths[:2]:
            with self.subTest(optional_dependency=compose_path):
                compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
                dependency = compose["services"]["web-proxy"]["depends_on"]["proxylogin"]
                self.assertEqual(dependency["condition"], "service_started")
                self.assertFalse(dependency["required"])

    def test_proxy_image_validates_activated_config_before_start(self) -> None:
        dockerfile = (DOCKER_ROOT / "all_in_one_kdcube/Dockerfile_ProxyOpenResty").read_text(
            encoding="utf-8"
        )
        managed_dockerfile = (
            DOCKER_ROOT / "custom-ui-managed-infra/Dockerfile_ProxyOpenResty"
        ).read_text(encoding="utf-8")
        config_tool = (DOCKER_ROOT / "all_in_one_kdcube/nginx/kdcube-nginx-config").read_text(
            encoding="utf-8"
        )

        self.assertIn("ARG KDCUBE_AI_APP_SOURCE_PATH=.", dockerfile)
        self.assertIn(
            "COPY ${KDCUBE_AI_APP_SOURCE_PATH}/deployment/docker/all_in_one_kdcube/nginx/kdcube-nginx-config",
            dockerfile,
        )
        self.assertIn("kdcube-nginx-config render", dockerfile)
        self.assertIn("ARG KDCUBE_AI_APP_SOURCE_PATH=.", managed_dockerfile)
        self.assertIn("kdcube-nginx-config render", managed_dockerfile)
        self.assertIn('openresty -t -c "$runtime_candidate"', config_tool)
        self.assertIn('deployment_sha="${NGINX_CONFIG_SHA256:-}"', config_tool)
        self.assertIn("does not match this task definition", config_tool)
        self.assertIn("required by this task definition is missing", config_tool)
        self.assertIn('if [ "$candidate_sha" != "$NGINX_CONFIG_SHA256" ]', config_tool)
        self.assertIn("activate_template", config_tool)
        self.assertIn('flock -x 9', config_tool)
        self.assertIn('mv "$template_candidate" "$TEMPLATE_PATH"', config_tool)
        self.assertIn('exit 10', config_tool)
        self.assertIn('/run/kdcube/nginx-template.sha256', config_tool)

    def test_proxy_config_tool_resolves_from_release_and_local_build_contexts(self) -> None:
        relative_tool = Path("deployment/docker/all_in_one_kdcube/nginx/kdcube-nginx-config")
        release_source = AI_APP_ROOT / relative_tool
        local_source = REPOSITORY_ROOT / "app/ai-app" / relative_tool

        self.assertTrue(release_source.is_file())
        self.assertTrue(local_source.is_file())
        self.assertEqual(release_source.resolve(), local_source.resolve())

        for compose_path in (
            DOCKER_ROOT / "all_in_one_kdcube/docker-compose.yaml",
            DOCKER_ROOT / "custom-ui-managed-infra/docker-compose.yaml",
        ):
            with self.subTest(compose=compose_path):
                compose = compose_path.read_text(encoding="utf-8")
                self.assertIn("KDCUBE_AI_APP_SOURCE_PATH=app/ai-app", compose)

    def test_reference_configs_forward_one_normalized_scheme(self) -> None:
        local_configs = (
            "all_in_one_kdcube/nginx/conf/nginx_proxy.conf",
            "all_in_one_kdcube/nginx/conf/nginx_proxy_delegated.conf",
            "custom-ui-managed-infra/nginx/conf/nginx_proxy.conf",
            "custom-ui-managed-infra/nginx/conf/nginx_proxy_delegated.conf",
        )
        ecs_reference = (
            DOCKER_ROOT / "custom-ui-managed-infra/nginx/conf/nginx_proxy_ecs.conf"
        ).read_text(encoding="utf-8")

        for relative_path in local_configs:
            with self.subTest(config=relative_path):
                local_config = (DOCKER_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("KDCUBE_FORWARDED_PROTO_SOURCE: request", local_config)
                self.assertIn("map $scheme $forwarded_proto_last", local_config)
                self.assertIn(
                    "proxy_set_header X-Forwarded-Proto $forwarded_proto;",
                    local_config,
                )
                self.assertIn(
                    "location ^~ ${ROUTE_PREFIX}/ {",
                    local_config,
                )
                self.assertNotIn(
                    "proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;",
                    local_config,
                )
        self.assertIn("map $http_x_forwarded_proto $viewer_proto_last", ecs_reference)
        self.assertIn("location ^~ ${ROUTE_PREFIX}/ {", ecs_reference)
        self.assertIn("proxy_set_header X-Forwarded-Proto $forwarded_proto;", ecs_reference)
        self.assertIn("set_real_ip_from  <ALB_CIDR>;", ecs_reference)
        self.assertNotIn("set_real_ip_from  ${ALB_CIDR};", ecs_reference)
        self.assertNotIn(
            "proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;",
            ecs_reference,
        )

    def test_application_site_route_matrix_is_generated_and_current(self) -> None:
        subprocess.run(
            [sys.executable, str(ROUTE_GENERATOR), "--check"],
            cwd=REPOSITORY_ROOT,
            check=True,
        )

        for template_path in PROXY_ROUTE_TEMPLATES:
            with self.subTest(config=template_path):
                template = template_path.read_text(encoding="utf-8")
                self.assertEqual(template.count("KDCUBE_APPLICATION_SITE_SELECTOR:BEGIN"), 1)
                self.assertEqual(template.count("KDCUBE_APPLICATION_SITE_ROUTES:BEGIN"), 1)
                self.assertIn("location = /health {", template)
                self.assertIn("location = ${ROUTE_PREFIX} {", template)
                self.assertIn("location ^~ ${ROUTE_PREFIX}/ {", template)
                self.assertIn(
                    "rewrite ^/sites/(.*)$ /api/integrations/sites/$1 break;",
                    template,
                )
                self.assertIn("rewrite ^/$ /api/integrations/site-root break;", template)
                self.assertIn(
                    "rewrite ^/(.*)$ /api/integrations/site-root/$1 break;",
                    template,
                )
                self.assertIn(
                    "proxy_set_header X-Forwarded-Host  $http_host;",
                    template,
                )
                self._assert_oauth_discovery_routes(template)
                self._assert_platform_session_route(template)

    def _assert_platform_session_route(self, config: str) -> None:
        """The platform's browser sign-in (login, callback, status, logout)
        lives on the chat ingress under /api/platform/; without a proxy route
        the callback lands on the application-site fallback as 200 HTML."""
        self.assertEqual(config.count("location ^~ /api/platform/ {"), 1)
        routes = config.index("KDCUBE_APPLICATION_SITE_ROUTES:BEGIN")
        self.assertLess(
            config.index("location ^~ /api/platform/ {", routes),
            config.index("location / {", routes),
            "the platform session route must precede the site fallback",
        )

    def _assert_oauth_discovery_routes(self, config: str) -> None:
        """OAuth discovery must never fall through to the site fallback.

        Claude Code and other MCP clients probe the RFC 9728 and RFC 8414
        well-known paths at the origin root before, or instead of, following
        the 401 challenge. The fallback answers unknown paths with the SPA's
        200 HTML, which a client fails to parse as JSON, so the proxy answers
        the pathless forms with an exact 404, maps the path-inserted forms onto
        the bundle OAuth surface, and 404s every other /.well-known/ path.
        """
        for needle in (
            "location = /.well-known/oauth-protected-resource {",
            "location ^~ /.well-known/oauth-protected-resource/ {",
            'rewrite "^/[.]well-known/oauth-protected-resource(/api/integrations/bundles/'
            '[^/]+/[^/]+/[^/]+/public)/(.+?)/?$" "$1/oauth/.well-known/oauth-protected-resource'
            "?resource=",
            "location = /.well-known/oauth-authorization-server {",
            "location ^~ /.well-known/oauth-authorization-server/ {",
            'rewrite "^/[.]well-known/oauth-authorization-server(/api/integrations/bundles/'
            '[^/]+/[^/]+/[^/]+/public/oauth)/?$" "$1/.well-known/oauth-authorization-server" break;',
            "location ^~ /.well-known/ {",
            'return 404 "{\\"detail\\":\\"Not Found\\"}";',
        ):
            self.assertIn(needle, config)
        routes = config.index("KDCUBE_APPLICATION_SITE_ROUTES:BEGIN")
        self.assertLess(
            config.index("location ^~ /.well-known/ {", routes),
            config.index("location / {", routes),
            "the well-known catch-all must precede the site fallback",
        )
        metadata_route = config.split(
            "location ^~ /.well-known/oauth-protected-resource/ {", 1
        )[1].split("}", 1)[0]
        self.assertIn("proxy_pass http://chat_proc;", metadata_route)

    def test_helm_proxy_uses_the_same_generated_route_contract(self) -> None:
        chart = (
            AI_APP_ROOT
            / "deployment/kubernetes/local/charts/kdcube-platform/templates/runtime-configmaps.yaml"
        ).read_text(encoding="utf-8")

        self.assertEqual(chart.count("KDCUBE_APPLICATION_SITE_SELECTOR:BEGIN"), 1)
        self.assertEqual(chart.count("KDCUBE_APPLICATION_SITE_ROUTES:BEGIN"), 1)
        self.assertIn("location = /health {", chart)
        self.assertIn("location = {{ $routePrefix }} {", chart)
        self.assertIn("location ^~ {{ $routePrefix }}/ {", chart)
        self.assertIn("rewrite ^/$ /api/integrations/site-root break;", chart)
        self.assertIn("rewrite ^/sites/(.*)$ /api/integrations/sites/$1 break;", chart)
        self._assert_oauth_discovery_routes(chart)


if __name__ == "__main__":
    unittest.main()
