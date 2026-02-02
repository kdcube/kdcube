## Prepare the bundles root

We mount a **single host directory** that contains all agentic bundles into the container at `${AGENTIC_BUNDLES_ROOT}` (default `/bundles`). Your `AGENTIC_BUNDLES_JSON` must reference **container paths** under that root.

### Option A — Copy (recommended)

Put your bundles into the root once (or keep them synced with rsync):

```bash
# Example
export SITE_AGENTIC_BUNDLES_ROOT="/abs/path/to/agentic-bundles-root"

mkdir -p "$SITE_AGENTIC_BUNDLES_ROOT/bundles" \
         "$SITE_AGENTIC_BUNDLES_ROOT/customers"

# Copy your sources/wheels/zips into the root
rsync -a /Users/you/src/my_bundle/                   "$SITE_AGENTIC_BUNDLES_ROOT/bundles/"
rsync -a /Users/you/src/>customer>/chatbot/          "$SITE_AGENTIC_BUNDLES_ROOT/customers/"
# For wheels/zips: just place the artifact in a subfolder under the root
# e.g. $SITE_AGENTIC_BUNDLES_ROOT/packages/acme_bundle-1.0.0.whl
```

**Why copy?** Symlinks that point outside the mounted root will break inside the container (target won’t exist). Copying (or syncing) avoids that.

### Option B — Symlinks (only if the target is *inside* the root)

Symlinks are preserved by Docker, but they must resolve **within the mounted root** once inside the container. If you symlink to a path outside the root (e.g. `/Users/...`), it won’t exist in the container.

✔️ OK:

```
/abs/agentic-bundles-root/bundles -> ./real/bundles   # relative symlink within root
/abs/agentic-bundles-root/real/bundles/...            # actual files live here under the root
```

❌ Not OK:

```
/abs/agentic-bundles-root/bundles -> /Users/you/other/location   # absolute outside root
```

### Directory layout example

```
$SITE_AGENTIC_BUNDLES_ROOT
├── bundles
│   ├── my_bundle/
│   │   ├── entrypoint.py
│   │   └── __init__.py
│   └── another_bundle/
│       ├── entrypoint.py
│       └── __init__.py
└── customers
    └── <customer>/
        ├── app_backend/
        │   ├── entrypoint.py
        │   └── __init__.py
        └── __init__.py
```

Then your `AGENTIC_BUNDLES_JSON` uses:
`/bundles/bundles`   (module `my_bundle.entrypoint`)
`/bundles/bundles`   (module `another_bundle.entrypoint`)
`/bundles/customers` (module `app_backend.entrypoint`)

---

# 4) Quick sanity checks

After `docker compose up -d`:

### Confirm Redis has the registry

```bash
docker exec -it chat-redis redis-cli -a "$REDIS_PASSWORD" \
  GET "kdcube:config:bundles:mapping:${TENANT_ID}:${DEFAULT_PROJECT_NAME}" | jq .
```

You should see the JSON with `default_bundle_id` and `bundles`.

### Confirm Chat sees it
This will work on hardcoded auth. Otherwise, you must use the chat client.
```bash
curl -s -H "Authorization: Bearer <token>" http://<chat-host>/landing/bundles | jq .
```

### Confirm container paths exist

```bash
docker exec -it chat-chat bash -lc 'ls -la /bundles && ls -la /bundles/bundles && ls -la /bundles/customers'
```

(Optional) test an import quickly (just checks Python importability, not decorator discovery):

```bash
docker exec -it chat-chat python - <<'PY'
import sys, importlib
sys.path.insert(0, "/bundles/bundles")
import my_bundle.entrypoint as a
print("✅ my_bundle OK:", a.__file__)
sys.path.insert(0, "/bundles/customers")
import app_backend.entrypoint as b
print("✅ <customer> app OK:", b.__file__)
PY
```

---

# 5) Notes/FAQs

* **Why container paths in the mapping?** So you can edit the mapping at runtime (via UI) without changing Compose; everything under `/bundles` is already mounted.
* **Do I still need `AGENTIC_BUNDLE_PATH` / `AGENTIC_BUNDLE_MODULE` envs?** No; we keep them only as a **fallback** if Redis doesn’t have a registry yet. Prefer managing bundles via the UI (super-admin) which persists to Redis and hot-applies via pub/sub.
* **.whl/.zip bundles**: place the artifact under the root (e.g. `/bundles/packages/acme-1.0.0.whl`) and set `"module"` to the package name inside the wheel/zip (e.g. `"acme_bundle"`). The loader handles adding the archive to `sys.path`.
* **Tenants/Projects**: we’re persisting under key `kdcube:config:bundles:mapping:<tenant>:<project>`. With a single preconfigured tenant/project, the UI just works; later you can expose selectors in UI and pass scope to the admin API.
