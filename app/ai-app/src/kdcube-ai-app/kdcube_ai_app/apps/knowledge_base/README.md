# Knowledge Base (KB)

> Modular tenant/project-scoped Knowledge Base with **multi-stage processing**, **enhanced hybrid search**, and **moderation endpoints**.
> SPDX-License-Identifier: MIT • © 2025 Elena Viter

---

The KB is a standalone service that allow the versioned content ingestion (URLs, Markdown, PDFs) by processing it into 
rich, navigable representations, and serves **high-precision search** with **source backtracking**. 
It exposes **REST** and **Socket.IO** interfaces, supports **local FS or S3 storage** as artifactory and logging storage
backends, and uses **PostgreSQL + pgvector** for search. It leverages KDCube accounting and traceability to support full audit.

---

## Features

* **Inputs**: `url`, `markdown`, `pdf`, `text` (to be extended with more formats)
* **Storage backends**: local filesystem or S3 (select via storage URI)
* **Processing pipeline (modular)**:

    * Extraction → Segmentation (continuous & retrieval) → Metadata (Optional) → Embedding → (optional) Summarization → Search Indexing
* **Enhanced hybrid search**:

    * BM25 (prefix-aware) → ANN k-NN fallback → semantic scoring → (optional) cross-encoder re-rank
    * Returns **navigation & backtrack info** (line/char ranges, heading path, RNs)
* **Precise highlighting** of extraction markdown with **line/position tracking**
* **Content-based deduplication** (content hash index)
* **Moderator endpoints** for upload, URL add, preview/download, delete
* **Health & stats** endpoints
* **Socket.IO**: real-time events for search/processing and multi-process fanout via Redis

---

## Architecture (high-level)

* **FastAPI** app: `apps/knowledge_base/api/web_app.py`
* **Processing pipeline**: `apps/knowledge_base/modules/*`
* **Core** (resource lifecycle + search helpers): `apps/knowledge_base/core.py`
* **Search engine** (DB-backed): `apps/knowledge_base/search.py` + `db/providers/knowledge_base_search.py`
* **Storage** (FS/S3 + collaborative layer): `apps/knowledge_base/storage.py`
* **Content index** (dedup): `index/content_index.py`
* **DB schema** (PostgreSQL, pgvector, pg\_trgm): `db/providers/knowledge_base_db.py` & SQL deploy script

---

## Quick start

### Prereqs
[kb](../../readme/kb)

* Python 3.11+
* PostgreSQL with extensions: `vector`, `pg_trgm`, `btree_gin`
* Redis (Socket.IO manager / pubsub)
* (Optional) S3 if using object storage


### Environment (minimal)

```bash
export TENANT_ID=home_tenant
export DEFAULT_PROJECT_NAME=default-project
export KDCUBE_STORAGE_PATH=/abs/path/to/storage       # or s3://bucket/prefix
export KB_APP_PORT=8000
export REDIS_URL=redis://:@localhost:6379/0
# Auth provider vars (IdP) if required by your setup
```

### Run

```bash
uvicorn kdcube_ai_app.apps.knowledge_base.api.web_app:app \
  --host 0.0.0.0 --port ${KB_APP_PORT:-8000} --reload
```

---

## Storage layout

The KB organizes artifacts per **tenant/project** and per **stage**:

```
tenants/<tenant>/projects/<project>/knowledge_base/
├── data
│   ├── raw/                # original content (bytes or text)
│   ├── extraction/         # marker/md extraction (e.g., extraction_0.md)
│   ├── segmentation/       # continuous & retrieval segments (+ JSON)
│   ├── metadata/           # derived metadata
│   ├── embedding/          # per-segment vectors
│   └── search_indexing/    # index state for reconciliation
└── log/knowledge_base/YYYY/MM/DD/operations.jsonl
```

Each resource is versioned and addressed by an **RN (Resource Name)**:

```
ef:<tenant>:<project>:knowledge_base:<stage>:<resource_id>:<version>[:<extra>]
# examples:
ef:home_tenant:default-project:knowledge_base:raw:url|www.example.com_article:1
ef:home_tenant:default-project:knowledge_base:extraction:url|...:1:extraction_0.md
ef:home_tenant:default-project:knowledge_base:segmentation:retrieval:url|...:1:segments.json
```

---

## Database

**PostgreSQL schema** per project (schema name derived from project). The deploy script creates:

* `datasource(id, version, title, uri, system_uri, metadata, status, expiration, …)`
* `retrieval_segment(id, version, resource_id, content, title, entities, tags, search_vector, embedding VECTOR(1536), …)`
* `content_hash(name, value, type, …)`
* `events` (append-only audit)
* Triggers & views to maintain `search_vector` and filter **active (non-expired)** data.

> See the bundled SQL for `CREATE EXTENSION` and index creation. Adjust embedding dimension if needed.

---

## Processing pipeline

All stages are modular (`apps/knowledge_base/modules/*`):

1. **Extraction**

    * HTML/PDF → Markdown (`extraction_0.md`), assets, structured JSON
    * PDF uses **Marker** by default
2. **Segmentation**

    * Builds **continuous** and **retrieval** segments with **line/char** tracking
3. **Metadata**

    * Derives entities, tags, counts
   
5. **(Optional) Summarization**
    * Connected with extra costs. Summarize requested types of segments (by default, retrieval segments only).
   
5. **Embedding**

    * Per retrieval segment vectors (default dimension 1536; configurable)
    * If summary products are available, the summary will be a subject of embedding.
    * Otherwise, it's retrieval segment content that we embed

6. **Search indexing**

    * Synchronizes DB rows and indexes for search


Use `KnowledgeBase.process_resource()` or targeted helpers (e.g., `extract_only`, `index_for_search`).

---

## Hybrid search (enhanced)

`db/providers/knowledge_base_search.py:hybrid_pipeline_search`:

1. **BM25 high-recall** with **prefix-aware** `to_tsquery('english', ':*')`
   Catches variants (e.g., “mitigator” when user typed “mitigate”).
2. **ANN k-NN fallback** via pgvector for recall safety.
3. **Semantic scoring** on union of candidates (`1 - cosine_dist`).
4. **Optional cross-encoder re-rank** for final ordering and thresholding.

The service wraps this into `KnowledgeBase.search_component.search(...)` and returns **navigation-rich** results (with backtrack).

---

## REST API (selected)

Base path: `/api/kb` (also supports `/api/kb/{project}` forms)

### Search

* `POST /search` → basic enhanced results (with navigation info)
* `POST /search/enhanced` → same result objects (union of `SearchResult` and `NavigationSearchResult`)
* `POST /content/highlighted` → apply citation highlighting to extraction markdown (by **RN**)
* `POST /{project}/faiss/search` → direct FAISS calls (admin/test)

**Example**:

```bash
curl -X POST "http://localhost:8000/api/kb/default-project/search/enhanced" \
  -H "Content-Type: application/json" \
  -d '{"query":"zero trust architecture controls","top_k":5}'
```

### Registry / Moderation

* `POST /upload` (multipart) → store file; returns `resource_id` & metadata
  *Supports* Markdown & PDFs (Marker).
* `POST /add-url` → fetch & store URL (auto MIME detection)
* `GET  /resources` → list resources + processing status
* `GET  /resource/{resource_id}/preview` → inline or download (respects MIME, binary-safe)
* `GET  /resource/{resource_id}/download`
* `GET  /resource/{resource_id}/content?content_type=raw|extraction|segments`
* `DELETE /resource/{resource_id}`

### Orchestration (process & index)

* `POST /upload/process`
* `POST /add-url/process`
  Dispatches background processing with accounting; progress can be pushed via Socket.IO.

### Content by RN

* `POST /content/by-rn` → resolve RN to content (binary-aware), returns `preview_url` for binaries
* `POST /content/segment` → fetch a **base segment** by index with optional citation highlighting and neighbor context

### Health / Admin

* `GET /health` → service + orchestrator health, queue stats, KB stats
* `GET /health/process` → per-process heartbeat data
* `GET /admin/search/stats` → aggregated counts, FAISS cache info

---

## Socket.IO (real-time)

Mounted at `/socket.io`.

**Auth on connect** (no static tokens):

```json
{
  "bearer_token": "<access_token>",
  "id_token": "<id_token>",
  "project": "default-project",
  "tenant": "home_tenant"
}
```

**Events**

* Client → Server: `"kb_search"`
  Payload: `{ request_id, query, top_k, resource_id?, on_behalf_session_id?, project?, tenant? }`
* Server → Client: `"kb_search_result"`
  `{ request_id, query, results, total_results, project, session_id }`
* Server → Client: `"session_info"`, `"socket_error"`

For **service principals**, include `on_behalf_session_id` per message.

---

## Permissions

Fine-grained checks wrap both REST and WS:

* **Read**: `...knowledge_base:*;read` → search & list
* **Write**: `...knowledge_base:*;write` → upload/add/delete/process
* **Admin**: project management & stats endpoints

(Exact role/permission mapping depends on your IdP integration.)

---

## Python usage (programmatic)

```python
from kdcube_ai_app.apps.knowledge_base.core import KnowledgeBase
from kdcube_ai_app.infra.llm.llm_data_model import ModelRecord

kb = KnowledgeBase(
    tenant="home_tenant",
    project="default-project",
    storage_backend=f"{KDCUBE_STORAGE_PATH}/kb/tenants/home_tenant/projects/default-project/knowledge_base",
    embedding_model=ModelRecord(provider="openai", model_id="text-embedding-3-small", dim=1536),
)

# Add a URL
from kdcube_ai_app.tools.datasource import URLDataElement
meta = kb.add_resource(URLDataElement(url="https://example.com/article"))

# Process (extraction → indexing)
await kb.process_resource(meta.id, meta.version, stages=["extraction","segmentation","embedding","search_indexing"])
```

---

## Result anatomy (search)

Each result carries:

* `relevance_score`, `query`
* `segment` with text & metadata (including `base_segment_guids`)
* `search_metadata` → `{resource_id, version, ...}`
* `source_info` → heading path, citation text, char/line ranges
* **Backtrack** → RNs for `raw`, `extraction`, `segmentation` + navigation blocks
  Use `/content/highlighted` or `/content/segment` to materialize highlighted views.

---

## Code map

```
api/
  web_app.py                # FastAPI app + Socket.IO mount
  search/search.py          # REST search endpoints
  registry/registry.py      # Upload/URL/preview/download/delete + RN helpers
  socketio/kb.py            # Socket.IO handler (auth, rooms, events)
core.py                     # KnowledgeBase class & helpers
db/                         # DB connectors & providers
index/                      # content hash index (dedup)
modules/                    # extraction, segmentation, embedding, search_indexing, ...
storage.py                  # Storage backends (FS/S3) & collaborative API
search.py                   # SimpleKnowledgeBaseSearch facade
```

---

## Notes & tips

* **Binary safety**: for non-text resources, API returns `preview_url`/`download_url` instead of raw bytes.
* **Dedup**: the content index prevents duplicates by **content hash**, not just filename/URL.
* **Versioning**: all resources are versioned; processing is **per version**.
* **Expiration**: datasources can carry an `expiration` timestamp for cache lifecycle; views split active vs expired.

---

## License

MIT © 2025 Elena Viter
