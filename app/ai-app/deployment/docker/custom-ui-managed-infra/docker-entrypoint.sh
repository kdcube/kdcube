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

# W677: the secrets folder has ONE owner uid (KDCUBE_SECRETS_OWNER_UID, appuser by default). Entries created
# by root (docker exec sessions, the one-time migration, kdcube-secrets, a host-side copy shown as uid 0) are
# handed to that owner here, before the app starts. Only root-owned entries are re-owned and modes are not
# touched; an entry owned by any other uid stays refused by the application.
SECRETS_ROOT="${KDCUBE_SECRETS_RUNTIME_ROOT:-/config/secrets}"
SECRETS_OWNER_UID="${KDCUBE_SECRETS_OWNER_UID:-$APPUSER_UID}"
SECRETS_ROOT="${SECRETS_ROOT%/}"
# The target must be an explicit secrets directory: absolute, canonical (no symlink or ".." on the path),
# not "/", at least two levels deep; anything else is skipped before any traversal (review P1).
case "$SECRETS_ROOT" in
    /*/*) SECRETS_ROOT_OK=1 ;;
    *) SECRETS_ROOT_OK=0 ;;
esac
if [ "$SECRETS_ROOT_OK" = "1" ] && [ -d "$SECRETS_ROOT" ] && [ ! -L "$SECRETS_ROOT" ] \
        && [ "$(readlink -f "$SECRETS_ROOT")" = "$SECRETS_ROOT" ]; then
    # Only private, single-link, root-owned entries: 0700 folders, 0600 records and 0400 tombstones.
    find "$SECRETS_ROOT" -xdev -uid 0 \( \( -type d -perm 0700 \) -o \( -type f -links 1 \( -perm 0600 -o -perm 0400 \) \) \) \
        -exec chown "$SECRETS_OWNER_UID" {} + 2>/dev/null || true
fi

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

# Execute the main application as appuser
exec gosu "$APPUSER" "$@"
