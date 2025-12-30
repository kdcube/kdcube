#!/bin/bash
set -e

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

# Execute the main application as appuser
exec gosu "$APPUSER" "$@"
