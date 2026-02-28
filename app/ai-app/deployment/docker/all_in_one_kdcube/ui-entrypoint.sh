#!/usr/bin/env sh
set -e

CONFIG_PATH="/usr/share/nginx/html/config.json"

if [ -n "${FRONTEND_CONFIG_JSON:-}" ]; then
  printf '%s' "$FRONTEND_CONFIG_JSON" > "$CONFIG_PATH"
elif [ -n "${FRONTEND_CONFIG_S3_URL:-}" ]; then
  if echo "$FRONTEND_CONFIG_S3_URL" | grep -q '^s3://'; then
    aws s3 cp "$FRONTEND_CONFIG_S3_URL" "$CONFIG_PATH"
  else
    curl -fsSL "$FRONTEND_CONFIG_S3_URL" -o "$CONFIG_PATH"
  fi
fi

exec nginx -g "daemon off;"
