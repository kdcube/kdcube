# Isolated Code Execution - Operations Guide

This guide covers setup and configuration for running [AI-]generated Python code in isolated Docker containers with network isolation and privilege separation.

## Table of Contents

- [Overview](#overview)
- [ISO Runtime Readme](#iso-runtime-readme)
- [Deployment Modes](#deployment-modes)
- [Prerequisites](#prerequisites)
- [Mode 1: Chat Service on Host](#mode-1-chat-service-on-host-bare-metal)
- [Mode 2: Chat Service in Docker](#mode-2-chat-service-in-docker-docker-in-docker)
- [Environment Variables](#environment-variables)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

---

## Overview

The isolated execution system allows running untrusted AI-generated code safely by:
- **Network isolation** - Executor code cannot access the internet or internal services
- **Privilege separation** - Supervisor handles tool calls; untrusted code runs in isolated executor
- **Tool proxying (Docker mode)** - All tool calls are proxied via Unix socket to the supervisor
- **Read-only filesystem** - Container has read-only root, only workspace is writable

**Supported deployment modes:**
1. **Bare metal** - Chat service runs on host, spawns Docker containers for code execution
2. **Docker-in-Docker** - Chat service runs in Docker, spawns sibling containers for code execution
3. **Local isolation (subprocess)** - Some tools run in a local subprocess without a supervisor (see runtime modes doc)

---

## ISO Runtime Readme

For implementation details (runtime flow, supervisor/executor roles, mounts, permissions, env vars, and parallel-exec notes), see:

- [README-iso-runtime.md](../../sdk/runtime/isolated/README-iso-runtime.md)
- [README-runtime-modes-builtin-tools.md](../../sdk/runtime/isolated/README-runtime-modes-builtin-tools.md)

---

## Deployment Modes

| Mode | Chat Service Location | py-code-exec Location | Use Case |
|------|----------------------|----------------------|----------|
| **Bare Metal** | Host machine | Docker container | Development, small deployments |
| **Docker-in-Docker** | Docker container | Sibling Docker container | Production, cloud deployments |

---

## Prerequisites

### All Modes

1. **Docker** (20.10+)
```bash
   docker --version
```

2. **Docker Compose** (2.0+)
```bash
   docker compose version
```

3. **Build py-code-exec image:**
```bash
   cd deployment/docker/py-code-exec
   docker build -t py-code-exec:latest .
```

### Docker-in-Docker Mode Only

4. **Docker socket access** - Chat container needs access to `/var/run/docker.sock`
5. **Docker CLI in chat container** - See Dockerfile_Chat below

---

## Mode 1: Chat Service on Host (Bare Metal)

### Setup


#### 1. Build py-code-exec Image
[Image Dockerfile_Exec is here](../../../../../../../deployment/docker/all_in_one/Dockerfile_Exec)
```bash
cd /path/to/project/deployment/docker/py-code-exec
docker build -t py-code-exec:latest .
```

#### 2. Configure Environment

Create `.env.backend`:
```bash
# AWS Credentials (for supervisor)
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...           # Your AWS key
AWS_SECRET_ACCESS_KEY=xxx...         # Your AWS secret

# Execution settings
PY_CODE_EXEC_IMAGE=py-code-exec:latest
PY_CODE_EXEC_TIMEOUT=600             # 10 minutes
PY_CODE_EXEC_NETWORK_MODE=host       # Supervisor needs network

# No Docker-in-Docker path translation needed (running on host)
```

#### 3. Run Chat Service
```bash
# Install dependencies
pip install -r requirements-chat.txt

# Run service
python kdcube_ai_app/apps/chat/api/web_app.py
```

#### 4. Verify Execution

The service will:
- Create `/tmp/codegen_xxx` directories for workspaces
- Spawn `docker run py-code-exec:latest` when executing code
- Mount `/tmp/codegen_xxx` → container `/workspace`

**Test:**
```bash
# While service is running, trigger code execution
# Check for running containers:
docker ps | grep py-code-exec

# Check workspace (persists after execution):
ls -la /tmp/codegen_*
```

---

## Mode 2: Chat Service in Docker (Docker-in-Docker)

### Architecture
```
Host
├── docker-compose.yml
├── exec-workspace/          # Shared workspace (host ↔ containers)
├── kdcube-storage/              # Knowledge base data
└── bundles/                 # Agentic tool bundles

Containers:
├── chat-chat               # Chat service (spawns py-code-exec)
└── py-code-exec            # Code execution (sibling container)
```

### Setup

#### 1. Create Directory Structure
```bash
cd /path/to/deployment
mkdir -p exec-workspace kdcube-storage logs/chat
```

#### 2. Build Chat Image with Docker CLI

**File: `deployment/docker/all_in_one/Dockerfile_Chat`**
```dockerfile
# Multi-stage build for production Python application

# Stage 1: Build stage
FROM python:3.12-slim as builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    build-essential \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY services/kdcube-ai-app/requirements-chat.txt requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Production stage
FROM python:3.12-slim as production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install Docker CLI from official Docker repository
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce-cli \
        fonts-noto \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        libmagic1 \
        file \
        gosu \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY --from=builder /opt/venv /opt/venv

RUN python -m playwright install --with-deps chromium

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser

# Create exec-workspace directory with correct ownership
RUN mkdir -p /exec-workspace && \
    chown -R appuser:appuser /exec-workspace

COPY --chown=appuser:appuser services/kdcube-ai-app/ .

COPY deployment/docker/all_in_one/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "kdcube_ai_app/apps/chat/api/web_app.py"]
```

#### 3. Create Entrypoint Script

**File: `deployment/docker/all_in_one/docker-entrypoint.sh`**
```bash
#!/bin/bash
set -e

# Handle Docker socket permissions across platforms (macOS vs Linux)

DOCKER_SOCK="/var/run/docker.sock"
APPUSER="appuser"
APPUSER_UID=1000

echo "[entrypoint] Starting Docker-in-Docker setup..."

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

# Execute the main application as appuser
exec gosu "$APPUSER" "$@"
```

#### 4. Configure docker-compose.yml

**File: `deployment/cicd/local/docker-compose.yml`**
```yaml
version: '3.8'

services:
  chat:
    image: chat-chat:latest
    container_name: chat-chat
    build:
      context: ../../..
      dockerfile: deployment/docker/all_in_one/Dockerfile_Chat
    env_file:
      - path: ./.env.backend
        required: false
    environment:
      # Service connections
      - REDIS_HOST=redis
      - POSTGRES_HOST=postgres-db
      
      # Path mappings for Docker-in-Docker
      - HOST_KB_STORAGE_PATH=${HOST_KB_STORAGE_PATH}
      - HOST_BUNDLES_PATH=${SITE_AGENTIC_BUNDLES_ROOT}
      - HOST_EXEC_WORKSPACE_PATH=${HOST_EXEC_WORKSPACE_PATH}
      
      # Execution settings
      - PY_CODE_EXEC_NETWORK_MODE=host
      
    volumes:
      # Docker socket (for spawning sibling containers)
      - /var/run/docker.sock:/var/run/docker.sock
      
      # Shared data directories
      - ./kdcube-storage:/kdcube-storage
      - ./exec-workspace:/exec-workspace     # ← Shared execution workspace
      - ./logs/chat:/logs
      
      # Bundles (tool modules)
      - type: bind
        source: ${SITE_AGENTIC_BUNDLES_ROOT}
        target: ${AGENTIC_BUNDLES_ROOT}
      
      # AWS credentials (local dev only)
      - type: bind
        source: ${HOME}/.aws
        target: /home/appuser/.aws
        read_only: true
    
    depends_on:
      - redis
      - postgres-db
    networks:
      - chat-internal

  # ... other services (redis, postgres-db, etc.)

networks:
  chat-internal:
    driver: bridge

volumes:
  redis-data:
  postgres-data:
```

#### 5. Configure Environment Variables

**File: `.env.backend`**
```bash
# === AWS Credentials (Local Dev) ===
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1
# For local: Credentials read from ~/.aws (mounted in docker-compose)
# For prod EC2: Instance role via IMDS (no mount needed)

# === Docker-in-Docker Path Mappings ===
# These map container paths to actual host paths for sibling containers

# macOS (development):
HOST_KB_STORAGE_PATH=/Users/yourname/project/deployment/cicd/local/kdcube-storage
HOST_BUNDLES_PATH=/Users/yourname/project/bundles
HOST_EXEC_WORKSPACE_PATH=/Users/yourname/project/deployment/cicd/local/exec-workspace

# Linux (production):
# HOST_KB_STORAGE_PATH=/home/deploy/kdcube-storage
# HOST_BUNDLES_PATH=/home/deploy/bundles
# HOST_EXEC_WORKSPACE_PATH=/home/deploy/exec-workspace

# === Bundle Configuration ===
SITE_AGENTIC_BUNDLES_ROOT=/Users/yourname/project/bundles  # Host path
AGENTIC_BUNDLES_ROOT=/bundles                                # Container path

# === Execution Settings ===
PY_CODE_EXEC_IMAGE=py-code-exec:latest
PY_CODE_EXEC_TIMEOUT=600
PY_CODE_EXEC_NETWORK_MODE=host       # Supervisor needs Redis/Postgres access

# === Production EC2 Settings (uncomment for prod) ===
# AWS_EC2_METADATA_DISABLED=false
# NO_PROXY=169.254.169.254,localhost,127.0.0.1
# AWS_SDK_LOAD_CONFIG=1
```

#### 6. Build and Start
```bash
cd deployment/cicd/local

# Build images
docker compose build chat

# Start services
docker compose up -d chat redis postgres-db

# Check logs
docker compose logs -f chat
```

#### 7. Verify Docker-in-Docker
```bash
# 1. Check chat container can access Docker
docker exec -it chat-chat docker ps
# Should list host's Docker containers

# 2. Trigger code execution, then check for workspace
ls -la /path/to/deployment/cicd/local/exec-workspace/
# Should show: codegen_xxx/ or react_xxx/ directories

# 3. Check sibling container was created (during execution)
docker ps -a | grep py-code-exec
# Should show recent py-code-exec containers
```

---

## Environment Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `PY_CODE_EXEC_IMAGE` | Docker image for code execution | `py-code-exec:latest` |
| `PY_CODE_EXEC_TIMEOUT` | Execution timeout (seconds) | `600` |
| `PY_CODE_EXEC_NETWORK_MODE` | Docker network mode | `host` |

### Docker-in-Docker Variables

| Variable | Description | Example (macOS) | Example (Linux) |
|----------|-------------|-----------------|-----------------|
| `HOST_KB_STORAGE_PATH` | Host path for KB data | `/Users/you/deployment/kdcube-storage` | `/home/deploy/kdcube-storage` |
| `HOST_BUNDLES_PATH` | Host path for tool bundles | `/Users/you/bundles` | `/home/deploy/bundles` |
| `HOST_EXEC_WORKSPACE_PATH` | Host path for execution | `/Users/you/deployment/exec-workspace` | `/home/deploy/exec-workspace` |

### AWS Credentials

#### Local Development
```bash
# Option 1: Mount ~/.aws (recommended)
# In docker-compose.yml:
volumes:
  - type: bind
    source: ${HOME}/.aws
    target: /home/appuser/.aws
    read_only: true

# Option 2: Environment variables
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=xxx...
AWS_REGION=us-east-1
```

#### Production (EC2)
```bash
# Use instance role (no credentials needed)
AWS_REGION=eu-west-1
AWS_DEFAULT_REGION=eu-west-1
AWS_EC2_METADATA_DISABLED=false
NO_PROXY=169.254.169.254,localhost,127.0.0.1
AWS_SDK_LOAD_CONFIG=1
```

**Security Note:** Executor subprocess NEVER sees AWS credentials (filtered from environment).

---

## Verification

### Test Execution Flow

**1. Check Docker socket access (Docker-in-Docker only):**
```bash
docker exec -it chat-chat docker ps
# Should work without errors
```

**2. Trigger code execution:**
```bash
# Via API or UI, run a task that generates code
# Example: "Create a chart comparing X and Y"
```

**3. Check logs for execution:**
```bash
docker compose logs chat | grep "docker.exec"

# Look for:
# [docker.exec] Running in Docker-in-Docker mode
# [docker.exec] Container paths: workdir=/exec-workspace/codegen_xxx/pkg
# [docker.exec] Host paths: workdir=/path/to/host/exec-workspace/codegen_xxx/pkg
```

**4. Verify workspace was created:**
```bash
# Bare metal:
ls -la /tmp/codegen_*

# Docker-in-Docker:
ls -la /path/to/deployment/exec-workspace/codegen_*
```

**5. Check execution artifacts:**
```bash
# Look for:
cd /path/to/exec-workspace/codegen_xxx/out
ls -la
# Should contain:
# - result.json (final output)
# - *.json (tool call logs)
# - runtime.out.log, runtime.err.log
# - Any generated files (charts, documents, etc.)
```

### Verify Security

**Test network isolation:**
```bash
# Executor should NOT be able to reach network
# This is enforced by unshare(CLONE_NEWNET) - no verification needed from outside

# Verify supervisor CAN reach services:
docker logs chat-chat | grep "supervisor"
# Should show successful Redis/Postgres connections
```

**Test privilege separation:**
```bash
# Check executor runs as UID 1001 (in container logs):
docker logs <py-code-exec-container-id> | grep "UID"
```

---

## Troubleshooting

### Issue: `docker: command not found` in chat container

**Symptom:**
```
FileNotFoundError: [Errno 2] No such file or directory: 'docker'
```

**Solution:**
Ensure docker-ce-cli is installed in Dockerfile_Chat (see Dockerfile above).

---

### Issue: `Permission denied` accessing Docker socket

**Symptom:**
```
permission denied while trying to connect to the Docker daemon socket
```

**Solution:**
Check entrypoint script is running and adding appuser to docker group:
```bash
docker logs chat-chat | grep entrypoint
# Should show: "✅ Docker access verified for appuser"
```

---

### Issue: `mounts denied: path is not shared`

**Symptom:**
```
The path /exec-workspace/codegen_xxx is not shared from the host
```

**Solutions:**

1. **Missing volume mount in docker-compose.yml:**
```yaml
   volumes:
     - ./exec-workspace:/exec-workspace  # ← Add this
```

2. **Missing HOST_EXEC_WORKSPACE_PATH:**
```bash
   # In .env.backend:
   HOST_EXEC_WORKSPACE_PATH=/full/path/to/exec-workspace
```

3. **Wrong path in translation:**
   Check logs for:
```
   [docker.exec] Host paths: workdir=/exec-workspace/...  # ❌ Wrong (container path)
   [docker.exec] Host paths: workdir=/Users/you/exec-workspace/...  # ✅ Correct (host path)
```

---

### Issue: Workspaces not visible on host

**Symptom:**
Execution completes but `/exec-workspace/codegen_xxx` doesn't exist on host.

**Solutions:**

1. **Check volume mount:**
```bash
   docker inspect chat-chat | grep exec-workspace
   # Should show: /path/to/host/exec-workspace:/exec-workspace
```

2. **Check workspace is being created in correct location:**
```bash
   docker exec -it chat-chat ls -la /exec-workspace/
   # Should show codegen_* directories
   
   # Then check on host:
   ls -la /path/to/host/exec-workspace/
   # Should match container listing
```

3. **Verify `get_exec_workspace_root()` is being used:**
   Check logs for:
```
   [solver.codegen] workdir=/exec-workspace/codegen_xxx  # ✅ Correct
   [solver.codegen] workdir=/tmp/codegen_xxx             # ❌ Wrong (old behavior)
```

---

### Issue: AWS credentials not working in executor

**This is expected behavior!** Executor should NOT have AWS credentials (security by design).

Only supervisor has credentials:
```bash
# Check supervisor logs (not executor):
docker logs chat-chat | grep "AWS"
# Should show credentials being passed to supervisor only
```

---

### Issue: Network tools failing

**Symptom:**
```
ConnectionError: Failed to reach https://api.example.com
```

**Expected:** Network tools (web_search, web_fetch) run in supervisor, NOT executor.

**Check:**
```bash
# Verify supervisor has network access:
docker logs chat-chat | grep "supervisor"

# Executor should use Unix socket to call network tools:
docker logs <py-code-exec> | grep "tool_call"
```

---

## Production Checklist

- [ ] py-code-exec image built and tagged
- [ ] Docker-in-Docker enabled (socket mounted)
- [ ] entrypoint.sh script in place and executable
- [ ] exec-workspace directory created with correct permissions
- [ ] Environment variables configured (paths, AWS region)
- [ ] AWS credentials configured (instance role for EC2)
- [ ] Volume mounts verified (exec-workspace, kdcube-storage, bundles)
- [ ] Test execution completes successfully
- [ ] Workspace files visible on host
- [ ] Tool calls logged correctly
- [ ] Security verified (executor isolated, supervisor has access)

---
