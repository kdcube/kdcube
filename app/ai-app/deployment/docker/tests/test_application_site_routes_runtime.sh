#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
IMAGE="${1:-kdcube-proxy-contract-test}"
NORMAL_CONFIG="$ROOT_DIR/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy.conf"
DELEGATED_CONFIG="$ROOT_DIR/deployment/docker/all_in_one_kdcube/nginx/conf/nginx_proxy_delegated.conf"
STUB_CONFIG="$ROOT_DIR/deployment/docker/tests/fixtures/application-site-upstreams.conf"
STATE_DIR="$(mktemp -d)"
NETWORK="kdcube-site-routes-$$"
STUB_CONTAINER="kdcube-site-upstreams-$$"
PROXY_CONTAINER=""

cleanup() {
  if [[ -n "$PROXY_CONTAINER" ]]; then
    docker stop "$PROXY_CONTAINER" >/dev/null 2>&1 || true
  fi
  docker stop "$STUB_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT

fail() {
  echo "$*" >&2
  exit 1
}

request_body() {
  local origin="$1"
  local path="$2"
  local host="$3"
  shift 3
  curl -fsS -H "Host: $host" "$@" "$origin$path"
}

assert_body() {
  local actual="$1"
  local expected="$2"
  local label="$3"
  [[ "$actual" == "$expected" ]] || fail "$label: expected '$expected', got '$actual'"
}

assert_request_header_capacity() {
  local origin="$1"
  local label="$2"
  local token
  local status

  # Synthetic values only: the parser must accept a field above its old 8 KiB
  # default while retaining the bounded 16 KiB field limit used at the edge.
  token="$(printf '%12000s' '' | tr ' ' x)"
  status="$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $token" "$origin/health")"
  assert_body "$status" "200" "$label 12000-byte token header"

  token="$(printf '%17000s' '' | tr ' ' x)"
  status="$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $token" "$origin/health")"
  assert_body "$status" "400" "$label oversized request header"
}

docker network create "$NETWORK" >/dev/null
docker run -d --rm \
  --name "$STUB_CONTAINER" \
  --network "$NETWORK" \
  --network-alias web-ui \
  --network-alias chat-ingress \
  --network-alias chat-proc \
  --network-alias metrics \
  --network-alias proxylogin \
  -v "$STUB_CONFIG:/usr/local/openresty/nginx/conf/nginx.conf:ro" \
  "$IMAGE" \
  openresty -g 'daemon off;' >/dev/null

run_proxy_matrix() {
  local route_prefix="$1"
  local forwarded_source="$2"
  local proxy_mode="${3:-normal}"
  local source_config="$NORMAL_CONFIG"
  local auth_args=()
  local safe_prefix
  local candidate
  local port
  local origin
  local location

  safe_prefix="$(printf '%s' "$route_prefix" | tr '/' '_')"
  if [[ "$proxy_mode" == "delegated" ]]; then
    source_config="$DELEGATED_CONFIG"
    auth_args=(-H 'Cookie: __Secure-LATC=test-auth; __Secure-LITC=test-id')
  fi
  candidate="$STATE_DIR/nginx-${safe_prefix}-${forwarded_source}-${proxy_mode}.conf"

  if [[ "$forwarded_source" == "request" ]]; then
    sed -e "s|\${ROUTE_PREFIX}|$route_prefix|g" "$source_config" > "$candidate"
  else
    sed \
      -e "s|\${ROUTE_PREFIX}|$route_prefix|g" \
      -e 's/KDCUBE_FORWARDED_PROTO_SOURCE: request/KDCUBE_FORWARDED_PROTO_SOURCE: trusted_x_forwarded_proto/' \
      -e 's/map \$scheme \$forwarded_proto_last/map \$http_x_forwarded_proto \$forwarded_proto_last/' \
      -e 's/default                        \$scheme;/default                        \$http_x_forwarded_proto;/' \
      "$source_config" > "$candidate"
  fi

  PROXY_CONTAINER="kdcube-site-proxy-${safe_prefix}-${forwarded_source}-${proxy_mode}-$$"
  docker run -d --rm \
    --name "$PROXY_CONTAINER" \
    --network "$NETWORK" \
    -p 127.0.0.1::80 \
    -v "$candidate:/usr/local/openresty/nginx/conf/nginx.conf:ro" \
    "$IMAGE" \
    openresty -g 'daemon off;' >/dev/null

  port="$(docker port "$PROXY_CONTAINER" 80/tcp | awk -F: 'NR == 1 {print $NF}')"
  origin="http://127.0.0.1:$port"

  for _ in $(seq 1 30); do
    if curl -fsS "$origin/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
  curl -fsS "$origin/health" >/dev/null || fail "proxy did not become ready"

  assert_request_header_capacity "$origin" "$proxy_mode $route_prefix $forwarded_source"

  assert_body \
    "$(request_body "$origin" /health app.example.test)" \
    "ok" \
    "proxy-owned health route"

  location="$(curl -fsSI -H 'Host: app.example.test' "$origin$route_prefix" | awk 'BEGIN {IGNORECASE=1} /^Location:/ {gsub("\r", "", $2); print $2}')"
  assert_body "$location" "$route_prefix/chat" "$route_prefix redirect"

  assert_body \
    "$(request_body "$origin" "$route_prefix/chat" app.example.test)" \
    "web-ui:/chat|host=app.example.test|xfh=app.example.test|proto=http" \
    "$route_prefix chat"
  assert_body \
    "$(request_body "$origin" "$route_prefix/assets/app.js" app.example.test)" \
    "web-ui:/assets/app.js|host=app.example.test|xfh=app.example.test|proto=http" \
    "$route_prefix asset"
  assert_body \
    "$(request_body "$origin" / app.example.test)" \
    "chat-proc:/api/integrations/site-root|host=app.example.test|xfh=app.example.test|proto=http" \
    "site root"
  assert_body \
    "$(request_body "$origin" /site.js app.example.test)" \
    "chat-proc:/api/integrations/site-root/site.js|host=app.example.test|xfh=app.example.test|proto=http" \
    "clean site path"
  assert_body \
    "$(request_body "$origin" /sites/sample-site app.example.test)" \
    "chat-proc:/api/integrations/sites/sample-site|host=app.example.test|xfh=app.example.test|proto=http" \
    "site alias"
  assert_body \
    "$(request_body "$origin" /sites/sample-site/site.js app.example.test)" \
    "chat-proc:/api/integrations/sites/sample-site/site.js|host=app.example.test|xfh=app.example.test|proto=http" \
    "site alias asset"
  assert_body \
    "$(request_body "$origin" /api/chat/ping app.example.test "${auth_args[@]}")" \
    "chat-ingress:/api/chat/ping|host=app.example.test|proto=http" \
    "reserved API"
  if [[ "$proxy_mode" == "delegated" ]]; then
    assert_body \
      "$(request_body "$origin" /auth/session app.example.test)" \
      "web-ui:/v1/session|host=app.example.test|xfh=|proto=http" \
      "delegated auth route"
  fi
  assert_body \
    "$(request_body "$origin" /sse/stream app.example.test "${auth_args[@]}")" \
    "chat-ingress:/sse/stream|host=app.example.test|proto=http" \
    "reserved SSE route"
  assert_body \
    "$(request_body "$origin" '/socket.io/?EIO=4&transport=polling' app.example.test "${auth_args[@]}")" \
    "chat-ingress:/socket.io/|host=app.example.test|proto=http" \
    "reserved Socket.IO route"

  if [[ "$forwarded_source" == "trusted_x_forwarded_proto" ]]; then
    assert_body \
      "$(request_body "$origin" /site.js public.example.test -H 'X-Forwarded-Proto: https')" \
      "chat-proc:/api/integrations/site-root/site.js|host=public.example.test|xfh=public.example.test|proto=https" \
      "trusted outer terminator"
  fi

  docker stop "$PROXY_CONTAINER" >/dev/null
  PROXY_CONTAINER=""
}

run_root_control_plane() {
  local candidate="$STATE_DIR/nginx-root-control-plane.conf"
  local port
  local origin
  local location

  sed -e 's|${ROUTE_PREFIX}|/|g' "$NORMAL_CONFIG" > "$candidate"
  PROXY_CONTAINER="kdcube-root-control-plane-$$"
  docker run -d --rm \
    --name "$PROXY_CONTAINER" \
    --network "$NETWORK" \
    -p 127.0.0.1::80 \
    -v "$candidate:/usr/local/openresty/nginx/conf/nginx.conf:ro" \
    "$IMAGE" \
    openresty -g 'daemon off;' >/dev/null

  port="$(docker port "$PROXY_CONTAINER" 80/tcp | awk -F: 'NR == 1 {print $NF}')"
  origin="http://127.0.0.1:$port"
  for _ in $(seq 1 30); do
    if curl -fsS "$origin/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done

  assert_body \
    "$(request_body "$origin" /health app.example.test)" \
    "ok" \
    "root-mode proxy health route"

  location="$(curl -fsSI -H 'Host: app.example.test' "$origin/" | awk 'BEGIN {IGNORECASE=1} /^Location:/ {gsub("\r", "", $2); print $2}')"
  assert_body "$location" "/chat" "root control-plane redirect"
  assert_body \
    "$(request_body "$origin" /chat app.example.test)" \
    "web-ui:/chat|host=app.example.test|xfh=app.example.test|proto=http" \
    "root control-plane route"

  docker stop "$PROXY_CONTAINER" >/dev/null
  PROXY_CONTAINER=""
}

run_proxy_matrix /platform request
run_proxy_matrix /control/ui request
run_proxy_matrix /platform trusted_x_forwarded_proto
run_proxy_matrix /platform request delegated
run_root_control_plane

echo "application-site proxy route behavior verified"
