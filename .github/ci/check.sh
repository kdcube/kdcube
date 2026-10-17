#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The checks a pull request must pass, run by .github/workflows/validate-kdcube-platform.yml
# and by `make check`, `make check-backend`, `make check-frontend`. (Root /scripts/ is gitignored,
# so the check lives here.)
#
#   .github/ci/check.sh backend    the service-free Python test suites
#   .github/ci/check.sh frontend   the chat web app's tests and type-checked build
#   .github/ci/check.sh all        both
#
# The backend expects the dependencies of app/ai-app/src/kdcube-ai-app/requirements-chat.txt
# (pytest, pytest-asyncio, connection-hub) and the frontend an `npm ci` in the web app.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SDK="$ROOT/app/ai-app/src/kdcube-ai-app"
WEB_APP="$ROOT/app/ai-app/ui/chat-web-app"

# Suites that need no PostgreSQL, Redis, network or credentials. A suite joins this list once it
# passes here; one that needs a service stays out until it has its own job.
BACKEND_SUITES=(
  kdcube_ai_app/storage/tests
  kdcube_ai_app/apps/chat/sdk/integrations/connection_hub/tests
)

backend() {
  cd "$SDK"
  PYTHONPATH="$SDK${PYTHONPATH:+:$PYTHONPATH}" python -m pytest -q -p no:cacheprovider "${BACKEND_SUITES[@]}"
}

frontend() {
  cd "$WEB_APP"
  npm test
  npm run build
}

case "${1:-all}" in
  backend) backend ;;
  frontend) frontend ;;
  all) backend; frontend ;;
  *) echo "usage: $0 [backend|frontend|all]" >&2; exit 2 ;;
esac
