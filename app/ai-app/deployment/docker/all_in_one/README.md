# All-In-One Docker Compose Setup

Quick reference guide for running the KDCube AI platform with Docker Compose.

## Services Overview

**Core Services** (started by default):
* `postgres-db` - PostgreSQL 16 with pgvector
* `postgres-setup` - Schema initialization (runs once)
* `redis` - Redis cache
* `neo4j` - Neo4j graph database
* `clamav` - Antivirus scanning
* `chat` - Chat API service
* `web-ui` - Frontend application (customizable)
* `web-proxy` - Nginx reverse proxy (customizable)

**Optional Services** (commented out by default):
* `kb` - Knowledge Base API
* `dramatiq` - Background task worker

## Customization Architecture

The platform supports three main customization points:

### 1. Custom Frontend (web-ui)
- **Location**: Customer repo with UI source code
- **Configured via**: `UI_BUILD_CONTEXT`, `UI_DOCKERFILE_PATH`, `UI_SOURCE_PATH`
- **Custom nginx**: `NGINX_UI_CONFIG_FILE_PATH`

### 2. Custom Reverse Proxy (web-proxy)
- **Location**: Dockerfile in platform repo, nginx config in customer repo
- **Configured via**: `PROXY_BUILD_CONTEXT`, `NGINX_PROXY_CONFIG_FILE_PATH`
- **Purpose**: Custom routing, SSL termination, rate limiting

### 3. Custom Agentic Apps (bundles)
- **Location**: Host filesystem directory
- **Configured via**: `HOST_BUNDLES_PATH` (host) → `AGENTIC_BUNDLES_ROOT` (container)
- **Purpose**: Extend chat service with custom Python apps, wheels, or bundles

## Directory Structure
```
deployment/docker/all_in_one/
├── docker-compose.yml               # Service orchestration
├── .env                             # Build contexts, paths, customization
├── .env.backend                     # Runtime config (DB, Redis, API keys)
├── docker-entrypoint.sh
├── Dockerfile_Chat
├── Dockerfile_KB
├── Dockerfile_Dramatiq
├── Dockerfile_Proxy                 # ← Platform-provided
├── Dockerfile_PostgresSetup
├── Dockerfile_Exec
├── data/                            # All persistent data (visible on host)
│   ├── kdcube-storage/              # Knowledge base storage
│   ├── exec-workspace/              # Code execution temporary files
│   ├── postgres/                    # PostgreSQL data
│   ├── redis/                       # Redis data
│   ├── clamav/                      # ClamAV virus definitions
│   └── neo4j/                       # Neo4j graph database
│       ├── data/
│       ├── logs/
│       ├── plugins/
│       └── import/
├── logs/                            # Service logs
│   ├── chat/
│   ├── kb/
│   └── dramatiq/
└── nginx/
    └── webroot/                     # For Let's Encrypt challenges

[Customers Repo Structure - Example]
/path/to/customer-solutions/
├── <customer-id>/ui/                    # ← UI source code
│   ├── package.json
│   ├── src/
│   └── ...
└── ops/cicd/customer-c/local/
    ├── Dockerfile_UI                # ← Customer-provided
    ├── .env.ui.build                # ← UI build env vars
    ├── nginx_ui.conf                # ← UI nginx config
    └── nginx_proxy.conf             # ← Proxy nginx config
```

## First-Time Setup

### 1. Create Required Directories
```bash
cd deployment/docker/all_in_one

# Create all data directories
mkdir -p data/{kdcube-storage,exec-workspace,postgres,redis,clamav,neo4j/{data,logs,plugins,import}}
chmod -R 0777 data

# Create log directories
mkdir -p logs/{chat,kb,dramatiq}

# Create nginx webroot
mkdir -p nginx/webroot
```

### 2. Configure Environment Variables

#### `.env` - Build Contexts & Paths
```bash
# =============================================================================
# Docker Compose Configuration Variables
# =============================================================================

# -----------------------------------------------------------------------------
# DOCKER-IN-DOCKER PATH MAPPINGS
# -----------------------------------------------------------------------------
HOST_KB_STORAGE_PATH=/path/to/deployment/docker/all_in_one/data/kdcube-storage
HOST_BUNDLES_PATH=/path/to/bundles                    # Your agentic apps
HOST_EXEC_WORKSPACE_PATH=/path/to/deployment/docker/all_in_one/data/exec-workspace
AGENTIC_BUNDLES_ROOT=/bundles                          # Mount path in container

# -----------------------------------------------------------------------------
# WEB UI BUILD CONFIGURATION
# -----------------------------------------------------------------------------
UI_BUILD_CONTEXT=/path/to/customer-repo               # Customer repo root
UI_DOCKERFILE_PATH=ops/cicd/customer-c/prod/Dockerfile_UI
UI_ENV_FILE_PATH=/path/to/customer-solutions/ops/cicd/customer-c/prod/.env.ui.build
# Build arguments (relative to UI_BUILD_CONTEXT)
UI_SOURCE_PATH=<customer-id>/ui                            # UI source location
UI_ENV_BUILD_RELATIVE=ops/cicd/customer-c/prod/.env.ui.build
NGINX_UI_CONFIG_FILE_PATH=ops/cicd/customer-c/prod/nginx_ui.conf

# -----------------------------------------------------------------------------
# PROXY BUILD CONFIGURATION
# -----------------------------------------------------------------------------
PROXY_BUILD_CONTEXT=/path/to/common-parent             # Common parent of both repos
PROXY_DOCKERFILE_PATH=platform-repo/deployment/docker/all_in_one/Dockerfile_Proxy
NGINX_PROXY_CONFIG_FILE_PATH=customer-solutions/ops/cicd/customer-c/prod/nginx_proxy.conf

# -----------------------------------------------------------------------------
# NEO4J CONFIGURATION
# -----------------------------------------------------------------------------
N4J_USER=neo4j
N4J_PASSWORD=changeme_neo4j_password
N4J_PAGECACHE=1G
N4J_HEAP_INITIAL=512m
N4J_HEAP_MAX=1G
```

#### `.env.backend` - Runtime Configuration
```bash
# Database
POSTGRES_HOST=postgres-db
POSTGRES_PORT=5444
POSTGRES_USER=kdcube
POSTGRES_PASSWORD=changeme_postgres_password
POSTGRES_DB=kdcube_ai

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=changeme_redis_password

# Neo4j Application Connection
APP_NEO4J_URI=bolt://neo4j:7687
APP_NEO4J_USERNAME=neo4j
APP_NEO4J_PASSWORD=changeme_neo4j_password

# AWS Credentials (local dev)
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1

# Code Execution
PY_CODE_EXEC_IMAGE=py-code-exec:latest
PY_CODE_EXEC_TIMEOUT=600
PY_CODE_EXEC_NETWORK_MODE=host

# Anthropic API
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here

# Application
APP_ENV=development
APP_DEBUG=false
APP_LOG_LEVEL=INFO
```

### 3. Build Code Execution Image
```bash
docker build -t py-code-exec:latest -f Dockerfile_Exec ../../..
```

### 4. Initialize Database
```bash
docker compose build postgres-setup
docker compose run --rm postgres-setup
```

## File Locations Summary

**Production server:**
```
/home/ubuntu/dev/src/kdcube-ai-app/app/ai-app/deployment/docker/all_in_one/
├── docker-compose.yml    ← From local
├── .env                  ← From local (production version)
├── .env.backend          ← From local (production version)
└── Dockerfile_Proxy      ← Already in repo

/home/ubuntu/dev/src/customer-solutions/ops/customer-c/dockercompose/prod
├── Dockerfile_UI         ← Already in repo
├── .env.ui.build         ← From local
├── nginx_ui.conf         ← From local
└── nginx_proxy.conf      ← From local
```

## Running Services

### Start All Services
```bash
cd deployment/docker/all_in_one

# Build all images
docker compose build

# Start services
docker compose up -d

# Watch logs
docker compose logs -f chat

# Check status
docker compose ps
```

### Stop Services
```bash
docker compose down

# Stop and remove volumes (⚠️ deletes all data)
docker compose down -v
```

## Customization Guide

### Adding Custom Frontend

1. **Create customer repo structure:**
```
   customer-repo/
   ├── your-ui-app/
   │   ├── package.json
   │   └── src/
   └── ops/deployment/
       ├── Dockerfile_UI
       ├── .env.ui.build
       └── nginx_ui.conf
```

2. **Create Dockerfile_UI** (fully parameterized):
```dockerfile
   FROM node:22-alpine AS builder
   
   ARG UI_SOURCE_PATH
   ARG UI_ENV_BUILD_RELATIVE
   
   WORKDIR /app
   COPY ${UI_SOURCE_PATH}/package*.json ./
   RUN npm ci --only=production=false
   COPY ${UI_SOURCE_PATH} .
   COPY ${UI_ENV_BUILD_RELATIVE} .env
   RUN npm run build
   
   FROM nginx:alpine
   RUN rm -rf /usr/share/nginx/html/*
   COPY --from=builder /app/dist /usr/share/nginx/html
   
   ARG NGINX_CONFIG_FILE_PATH
   COPY ${NGINX_CONFIG_FILE_PATH} /etc/nginx/nginx.conf
   
   EXPOSE 80
   CMD ["nginx", "-g", "daemon off;"]
```

3. **Update .env:**
```bash
   UI_BUILD_CONTEXT=/path/to/customer-repo
   UI_DOCKERFILE_PATH=ops/deployment/Dockerfile_UI
   UI_SOURCE_PATH=your-ui-app
   UI_ENV_BUILD_RELATIVE=ops/deployment/.env.ui.build
   NGINX_UI_CONFIG_FILE_PATH=ops/deployment/nginx_ui.conf
```

### Adding Custom Proxy Configuration

1. **Create nginx_proxy.conf** in customer repo:
```nginx
   http {
       upstream chat_api {
           server chat:8010;
       }
       
       server {
           listen 80;
           
           location /api/chat/ {
               proxy_pass http://chat_api/api/chat/;
               # ... proxy settings
           }
           
           location / {
               proxy_pass http://web-ui;
           }
       }
   }
```

2. **Update .env:**
```bash
   PROXY_BUILD_CONTEXT=/path/to/common-parent
   PROXY_DOCKERFILE_PATH=platform-repo/deployment/docker/all_in_one/Dockerfile_Proxy
   NGINX_PROXY_CONFIG_FILE_PATH=customer-solutions/ops/deployment/nginx_proxy.conf
```

### Adding Custom Agentic Apps (Bundles)

1. **Create bundles directory:**
```
   /path/to/bundles/
   ├── my-custom-app/
   │   ├── __init__.py
   │   └── agent.py
   ├── custom-wheel-1.0.0-py3-none-any.whl
   └── another-bundle.zip
```

2. **Update .env:**
```bash
   HOST_BUNDLES_PATH=/path/to/bundles
   AGENTIC_BUNDLES_ROOT=/bundles
```

3. **Bundles are automatically mounted** into chat container at `/bundles`

## Development Workflow

### Rebuild Specific Service

**Chat Service:**
```bash
docker compose stop chat && docker compose rm chat -f && docker compose up chat -d --build
```

**Web UI:**
```bash
docker compose stop web-ui && docker compose rm web-ui -f && docker compose up web-ui -d --build
```

**ProxyLogin:**
```bash
docker compose stop proxylogin && docker compose rm proxylogin -f && docker compose build proxylogin --no-cache && docker compose up proxylogin -d
```

**Proxy:**
```bash
docker compose stop web-proxy && docker compose rm web-proxy -f && docker compose up web-proxy -d --build
```

### Rebuild Code Execution Image
```bash
docker build -t py-code-exec:latest -f Dockerfile_Exec ../../..
```

### Re-run Database Migrations
```bash
docker compose build postgres-setup && docker compose run --rm postgres-setup
```

### View Service Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f chat
docker compose logs -f web-ui
docker compose logs -f web-proxy
```

## Troubleshooting

### Check Service Health
```bash
docker compose ps
```

Look for services with `(healthy)` status.

### Verify postgres-setup Completed
```bash
docker compose ps postgres-setup
```

Should show `exited (0)` status.

### Build Context Issues

If you get "file not found" errors during build:

1. **Check build context path:**
```bash
   echo $UI_BUILD_CONTEXT
   ls -la $UI_BUILD_CONTEXT
```

2. **Verify relative paths exist:**
```bash
   ls -la $UI_BUILD_CONTEXT/$UI_SOURCE_PATH
   ls -la $UI_BUILD_CONTEXT/$NGINX_UI_CONFIG_FILE_PATH
```

3. **Test build manually:**
```bash
   docker build \
     --build-context ${UI_BUILD_CONTEXT} \
     -f ${UI_BUILD_CONTEXT}/${UI_DOCKERFILE_PATH} \
     --build-arg UI_SOURCE_PATH=${UI_SOURCE_PATH} \
     --build-arg NGINX_CONFIG_FILE_PATH=${NGINX_UI_CONFIG_FILE_PATH} \
     ${UI_BUILD_CONTEXT}
```

### Reset Everything
```bash
# Stop and remove all containers + volumes
docker compose down -v

# Recreate directories
rm -rf data logs
mkdir -p data/{kdcube-storage,exec-workspace,postgres,redis,clamav,neo4j/{data,logs,plugins,import}}
mkdir -p logs/{chat,kb,dramatiq} nginx/webroot
chmod -R 0777 data

# Start fresh
docker compose build postgres-setup && docker compose run --rm postgres-setup
docker compose build
docker compose up -d
```

### Common Issues

**Port conflicts:**
```bash
lsof -i :8000  # Chat API
lsof -i :80    # Web Proxy
lsof -i :5432  # Postgres
```

**Permission denied on Docker socket:**
```bash
# Add your user to docker group (Linux)
sudo usermod -aG docker $USER
newgrp docker
```

**npm build script not found:**
Check the correct script name in your UI's package.json and update Dockerfile_UI accordingly.

## Key Architecture Notes

1. **Two build contexts**: UI uses customer repo, Proxy uses common parent (can reach both repos)
2. **All paths in .env**: No hardcoded defaults in Dockerfiles
3. **Build args flow**: .env → docker-compose → Dockerfile
4. **Neo4j credentials**: Must be in **both** `.env` (interpolation) and `.env.backend` (runtime)
5. **Data persistence**: Everything under `data/` directory
6. **Chat listens on port 8010** inside container, mapped to 8000 on host
7. **Postgres uses port 5444** inside container (overridden in .env.backend)

## Architecture Diagram
```
┌─────────────┐
│  web-proxy  │ :80, :443 (customizable nginx)
└─────┬───────┘
      │
      ├─────────────────┐
      │                 │
┌─────▼─────┐    ┌──────▼──────┐
│  web-ui   │    │    chat     │ :8010
│(custom)   │    │  + bundles  │
└───────────┘    └──────┬──────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
   ┌────▼────┐    ┌─────▼─────┐   ┌────▼────┐
   │  redis  │    │ postgres  │   │  neo4j  │
   └─────────┘    └───────────┘   └─────────┘
```

## Production Deployment Notes

For production deployment:

1. Update `.env` paths to Linux equivalents
2. Remove `~/.aws` volume mount from chat service (use EC2 instance role)
3. Update `.env.backend` for AWS instance role:
```bash
   AWS_EC2_METADATA_DISABLED=false
   NO_PROXY=169.254.169.254,localhost,127.0.0.1
```
4. Configure SSL certificates in nginx_proxy.conf
5. Set up Let's Encrypt volume mounts

## Migration between volume-based and data folder-centric mounting

### Find all volumes
List all volumes
```shell
docker volume ls
```

Find your volumes (likely named like: all_in_one_postgres-data, all_in_one_redis-data, etc.)
```shell
docker volume ls | grep -E 'postgres|redis|neo4j|clamav'
```

### Create target directories
```shell
cd /home/ubuntu/dev/src/kdcube-ai-app/app/ai-app/deployment/docker/all_in_one
```

Create target directories
```shell
mkdir -p data/{postgres,redis,clamav,neo4j/{data,logs,plugins,import}}
chmod -R 0777 data
```

### Copy Data from Volumes to Bind Mounts
#### Method: Using temporary container to copy data
```shell
#!/bin/bash
# Migration script with automatic permission fixing

cd /home/ubuntu/dev/src/kdcube-ai-app/app/ai-app/deployment/docker/all_in_one

# Stop services first
docker compose down

# --- Postgres ---
echo "Migrating Postgres data..."
docker run --rm \
  -v all_in_one_postgres-data:/source \
  -v $(pwd)/data/postgres:/target \
  alpine sh -c "cp -av /source/. /target/ && chmod -R 777 /target"

# --- Redis ---
echo "Migrating Redis data..."
docker run --rm \
  -v all_in_one_redis-data:/source \
  -v $(pwd)/data/redis:/target \
  alpine sh -c "cp -av /source/. /target/ && chmod -R 777 /target"

# --- Neo4j Data ---
echo "Migrating Neo4j data..."
docker run --rm \
  -v all_in_one_neo4j-data:/source \
  -v $(pwd)/data/neo4j/data:/target \
  alpine sh -c "cp -av /source/. /target/ && chmod -R 777 /target"

# --- Neo4j Logs ---
echo "Migrating Neo4j logs..."
docker run --rm \
  -v all_in_one_neo4j-logs:/source \
  -v $(pwd)/data/neo4j/logs:/target \
  alpine sh -c "cp -av /source/. /target/ && chmod -R 777 /target"

# --- Neo4j Plugins ---
echo "Migrating Neo4j plugins..."
docker run --rm \
  -v all_in_one_neo4j-plugins:/source \
  -v $(pwd)/data/neo4j/plugins:/target \
  alpine sh -c "cp -av /source/. /target/ && chmod -R 777 /target"

# --- Neo4j Import ---
echo "Migrating Neo4j import..."
docker run --rm \
  -v all_in_one_neo4j-import:/source \
  -v $(pwd)/data/neo4j/import:/target \
  alpine sh -c "cp -av /source/. /target/ && chmod -R 777 /target"

# --- ClamAV ---
echo "Migrating ClamAV data..."
docker run --rm \
  -v all_in_one_clamav-db:/source \
  -v $(pwd)/data/clamav:/target \
  alpine sh -c "cp -av /source/. /target/ && chmod -R 777 /target"

# Fix ownership from host
echo "Fixing ownership..."
sudo chown -R ubuntu:ubuntu data/
sudo chmod -R 0777 data/

echo "Migration complete!"
echo "Verify data integrity before removing old volumes."
```

#### Verify Migration
```shell
# Check that data was copied
ls -la data/postgres/
ls -la data/redis/
ls -la data/neo4j/data/
ls -la data/clamav/

# Start services with new bind mounts
docker compose up -d

# Check logs
docker compose logs -f postgres-db
docker compose logs -f redis
docker compose logs -f neo4j

# Test database connection
docker compose exec postgres-db psql -U kdcube -d kdcube_ai -c '\dt'
```

#### Cleanup Old Volumes (ONLY AFTER VERIFYING)
```shell
# List volumes again
docker volume ls

# Remove old volumes (BE CAREFUL - THIS IS PERMANENT)
docker volume rm all_in_one_postgres-data
docker volume rm all_in_one_redis-data
docker volume rm all_in_one_neo4j-data
docker volume rm all_in_one_neo4j-logs
docker volume rm all_in_one_neo4j-plugins
docker volume rm all_in_one_neo4j-import
docker volume rm all_in_one_clamav-db
```