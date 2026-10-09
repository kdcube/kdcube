#!/bin/bash
set -e

# • Purpose of docker-entrypoint.sh:
#
#   1. Docker socket permissions
#      It reads the GID of /var/run/docker.sock, creates a matching group, and adds appuser to it. That’s needed because the processor spawns code‑exec containers via Docker.
#   2. Ensure write access to /exec-workspace
#      It chowns /exec-workspace so the processor can write.
#   3. Drops privileges
#      It uses gosu to run the service as appuser.

# This script handles Docker socket permissions across platforms
# Works on both macOS (no docker group) and Linux (varying docker GIDs)

DOCKER_SOCK="/var/run/docker.sock"
APPUSER="appuser"
APPUSER_UID=1000

echo "[entrypoint] Starting Docker-in-Docker setup..."

# Check if docker socket exists and is accessible
if [ -S "$DOCKER_SOCK" ]; then
    # Get the GID of the docker socket
    DOCKER_GID=$(stat -c '%g' "$DOCKER_SOCK" 2>/dev/null || stat -f '%g' "$DOCKER_SOCK" 2>/dev/null)

    echo "[entrypoint] Docker socket found with GID: $DOCKER_GID"

    # Check if a group with this GID already exists
    if getent group "$DOCKER_GID" >/dev/null 2>&1; then
        DOCKER_GROUP=$(getent group "$DOCKER_GID" | cut -d: -f1)
        echo "[entrypoint] Group '$DOCKER_GROUP' (GID $DOCKER_GID) already exists"
    else
        # Create a new group with the docker socket's GID
        DOCKER_GROUP="dockerhost"
        echo "[entrypoint] Creating group '$DOCKER_GROUP' with GID $DOCKER_GID"
        groupadd -g "$DOCKER_GID" "$DOCKER_GROUP" || true
    fi

    # Add appuser to the docker group
    echo "[entrypoint] Adding $APPUSER to group '$DOCKER_GROUP'"
    usermod -aG "$DOCKER_GROUP" "$APPUSER" || true

    # Verify access
    if su - "$APPUSER" -c "docker ps >/dev/null 2>&1"; then
        echo "[entrypoint] ✅ Docker access verified for $APPUSER"
    else
        echo "[entrypoint] ⚠️  Warning: Docker access test failed, but continuing..."
    fi
else
    echo "[entrypoint] ⚠️  Docker socket not found at $DOCKER_SOCK"
    echo "[entrypoint] Continuing without Docker-in-Docker support..."
fi

echo "[entrypoint] Switching to user $APPUSER (UID $APPUSER_UID)"

# Ensure appuser owns the exec workspace (volume overrides image ownership)
chown -R appuser:appuser /exec-workspace || true

# W677: the secrets folder has ONE owner uid (KDCUBE_SECRETS_OWNER_UID, appuser by default). Root-owned
# private entries (created by docker exec sessions, the one-time migration, kdcube-secrets, or a host copy
# shown as uid 0) are handed to that owner before the app starts. The repair and all its guards (explicit
# canonical target, trusted 0700 folder chain, single-link 0600/0400 files, no-follow descriptor walk,
# same filesystem, modes untouched) are kdcube_ai_app.infra.secrets.ownership_repair; anything else is
# left for the application to refuse. A failure never blocks the start.
SECRETS_ROOT="${KDCUBE_SECRETS_RUNTIME_ROOT:-/config/secrets}"
SECRETS_OWNER_UID="${KDCUBE_SECRETS_OWNER_UID:-$APPUSER_UID}"
python -m kdcube_ai_app.infra.secrets.ownership_repair "$SECRETS_ROOT" "$SECRETS_OWNER_UID" || true

# Ensure appuser can create managed bundle cache entries without recursively
# walking the shared cache on every start. Recursive repair is expensive on EFS
# and can block the HTTP server from binding long enough for service discovery
# to route traffic to a non-listening task.
MANAGED_BUNDLES_ROOT="${MANAGED_BUNDLES_ROOT:-/managed-bundles}"
if [ -d "$MANAGED_BUNDLES_ROOT" ]; then
    if [ "${CHOWN_MANAGED_BUNDLES_RECURSIVE:-0}" = "1" ]; then
        echo "[entrypoint] Recursively repairing managed bundle ownership: $MANAGED_BUNDLES_ROOT"
        chown -R appuser:appuser "$MANAGED_BUNDLES_ROOT" || true
    else
        chown appuser:appuser "$MANAGED_BUNDLES_ROOT" || true
    fi
fi

# Ensure appuser can access git SSH materials (used for git bundles)
GIT_KEY_SRC="/run/secrets/git_ssh_key"
GIT_KNOWN_HOSTS_SRC="/run/secrets/git_known_hosts"
APP_SSH_DIR="/home/${APPUSER}/.ssh"

if [ -f "$GIT_KEY_SRC" ]; then
    mkdir -p "$APP_SSH_DIR"
    chmod 700 "$APP_SSH_DIR"
    if chown "$APPUSER:$APPUSER" "$GIT_KEY_SRC" 2>/dev/null; then
        chmod 600 "$GIT_KEY_SRC" || true
        export GIT_SSH_KEY_PATH="$GIT_KEY_SRC"
    else
        APP_KEY_PATH="${APP_SSH_DIR}/kdcube_git_key"
        cp "$GIT_KEY_SRC" "$APP_KEY_PATH"
        chown "$APPUSER:$APPUSER" "$APP_KEY_PATH" || true
        chmod 600 "$APP_KEY_PATH" || true
        export GIT_SSH_KEY_PATH="$APP_KEY_PATH"
    fi
fi

if [ -f "$GIT_KNOWN_HOSTS_SRC" ]; then
    mkdir -p "$APP_SSH_DIR"
    chmod 700 "$APP_SSH_DIR"
    if chown "$APPUSER:$APPUSER" "$GIT_KNOWN_HOSTS_SRC" 2>/dev/null; then
        chmod 600 "$GIT_KNOWN_HOSTS_SRC" || true
        export GIT_SSH_KNOWN_HOSTS="$GIT_KNOWN_HOSTS_SRC"
    else
        APP_KNOWN_HOSTS="${APP_SSH_DIR}/known_hosts"
        cp "$GIT_KNOWN_HOSTS_SRC" "$APP_KNOWN_HOSTS"
        chown "$APPUSER:$APPUSER" "$APP_KNOWN_HOSTS" || true
        chmod 600 "$APP_KNOWN_HOSTS" || true
        export GIT_SSH_KNOWN_HOSTS="$APP_KNOWN_HOSTS"
    fi
fi

# Execute the main application as appuser
exec gosu "$APPUSER" "$@"
