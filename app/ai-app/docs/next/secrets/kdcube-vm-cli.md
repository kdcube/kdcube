# KDCube VM CLI (draft)

Date: 2026-03-07

## Goal
Run KDCube in a VM to isolate secrets and service data from the host user.
The CLI owns the VM lifecycle and runs Docker/Compose inside the VM.

## Core idea
- Everything sensitive runs inside the VM:
  - kdcube services
  - data volumes
  - secrets store / daemon
- The host only runs the CLI and the browser.

## VM backends (first target)
Pick one backend per OS:
- macOS: **Colima** (Lima) or Docker Desktop
- Windows: **WSL2** (or Docker Desktop)
- Linux: native or **Multipass**

The CLI should auto-detect installed backends and ask the user to pick one.

## CLI commands (v1)
- `kdcube setup --vm`
  - Create VM (if missing)
  - Configure docker engine
  - Prepare `/var/lib/kdcube` layout
  - Install secrets store
- `kdcube up`
  - Run `docker compose up -d` inside VM
- `kdcube down`
  - Run `docker compose down` inside VM
- `kdcube status`
  - Report VM + services status
- `kdcube logs [service]`
  - Stream logs from VM
- `kdcube vm start|stop|destroy`

## VM control strategy
Two options (v1 should pick one):

### Option A: SSH into VM (simple, portable)
- VM exposes SSH
- CLI executes:
  - `docker compose up -d`
  - `docker compose logs`
  - `docker compose down`

### Option B: Docker context
- CLI creates a docker context pointed at VM daemon
- CLI runs `docker compose` locally but targets the VM

## VM layout
Inside VM:
```
/var/lib/kdcube/
  config/
  data/
  secrets/
  bundles/
  logs/
```

Host:
```
~/.kdcube/
  vm/          # metadata only
  cli/         # no secrets
```

## Ports
Expose only required ports to host:
- UI: 5174
- Ingress: 8010
- Optional: pgadmin, metrics

## Secrets handling in VM
- Secrets are written to `/var/lib/kdcube/secrets` (encrypted)
- A secrets daemon exposes a local socket inside VM
- Services use `SECRETS_PROVIDER=local` and `SECRETS_SOCKET_PATH`

### Secrets injection options
Option A — **Installer runs inside VM** (cleanest):
- CLI creates VM, then runs `kdcube` inside VM via SSH.
- Secrets are entered inside the VM and never touch host disk.
- Secrets are stored in the VM’s local store / keychain.

Option B — **Host installer streams secrets into VM**:
- CLI prompts on host, then streams secrets directly to the VM secrets daemon.
- No host file writes; secrets are transmitted over SSH tunnel.
- Faster UX, but if the host is compromised during entry, secrets are exposed.

## UX flow (setup)
1. `kdcube setup --vm`
2. Detect backend → ask user to pick
3. Create VM (if missing)
4. Initialize VM layout
5. Ask for config + secrets (Option A: inside VM, Option B: streamed in)
6. Save config in `/var/lib/kdcube/config`
7. Start services
8. Print host URL

## UX flow (run)
1. `kdcube up`
2. Validate VM running
3. Run compose in VM
4. Print URLs + status

## Open questions
- Which backend should be primary on macOS?
- Should we support running in VM + local dev mode in the same CLI binary?
- Where should VM images be cached?
