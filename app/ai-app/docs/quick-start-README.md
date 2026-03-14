# Quick Start (Local Docker Compose)

This is the shortest end‑to‑end path to run the platform locally.

---

**1) Prepare the 5 deployment descriptors (short form)**

- **`assembly.yaml`** — platform + auth + infra + proxy + frontend.  
  Picks the platform release and defines auth, proxy, ports, and optional frontend build.  
  Doc: `app/ai-app/docs/service/cicd/assembly-descriptor-README.md`
- **`secrets.yaml`** — platform secrets (OpenAI, Anthropic, Git, DB passwords, etc.).  
  Doc: `app/ai-app/docs/service/cicd/secrets-descriptor-README.md`
- **`gateway.yaml`** — gateway capacity + throttling rules.  
  Doc: `app/ai-app/docs/service/cicd/gateway-config-README.md`
- **`bundles.yaml`** — bundle registry + non‑secret bundle config.  
  Bundles come from Git (default), you can add new bundles and override default bundle props.  
  Doc: `app/ai-app/docs/service/cicd/release-bundle-README.md`
- **`bundles.secrets.yaml`** — bundle secrets (dot‑path keys).  
  If a bundle is in a private repo, provide SSH key or HTTPS token here (or enter it in the CLI).  
  Doc: `app/ai-app/docs/service/cicd/release-bundle-README.md`

CI/CD overview: `app/ai-app/docs/service/cicd/custom-cicd-README.md`

---

**2) Run the CLI**

```bash
kdcube-setup
```

Doc: `app/ai-app/docs/service/cicd/cli-README.md`  

Notes:
- This creates a default workdir at `~/.kdcube/kdcube-runtime`.
- If you choose non‑local sources, the repo is pulled into `~/.kdcube/kdcube-ai-app`.
- The CLI writes env files into the workdir and starts Docker Compose.

---

**3) Docker Compose starts**

The CLI brings up the stack automatically (no manual compose needed).

**Important:** secrets are stored in the in‑memory secrets service.  
If services restart, re‑run the CLI so secrets are re‑injected.

---

**4) Open the UI**

Open the URL printed by the CLI.  

From the **Bundle Admin** you can:
- change the **default bundle**
- override **bundle props**
- set **bundle secrets**

For local installs, admin changes **do not survive restarts** (re‑apply via CLI or descriptors).

---

**5) Upgrade a bundle**

To pull a newer bundle:
- push the new version to Git
- update the `ref` in bundle admin (or in `bundles.yaml`)
- the system pulls the new version automatically

---

**6) Bundle storage locations (3 types)**

Bundles can use three storage types:
- **Local FS** (host)  
- **Cloud storage**  
- **Cache**

See: `app/ai-app/docs/sdk/bundle/bundle-storage-cache-README.md`

---

**Quick mental model**

```mermaid
flowchart LR
  A[Descriptors x5] -->|CLI| B[Workdir + Env]
  B --> C[Docker Compose]
  C --> D[Secrets Service (in‑memory)]
  C --> E[UI + Admin]
  E --> F[Bundles Registry + Props]

  classDef desc fill:#e3f2fd,stroke:#64b5f6,color:#0d47a1;
  classDef cli fill:#fff3e0,stroke:#ffb74d,color:#e65100;
  classDef svc fill:#e8f5e9,stroke:#81c784,color:#1b5e20;
  classDef ui fill:#f3e5f5,stroke:#ba68c8,color:#4a148c;
  classDef reg fill:#fce4ec,stroke:#f06292,color:#880e4f;

  class A desc;
  class B cli;
  class C svc;
  class D svc;
  class E ui;
  class F reg;
```

---

**That’s it**  
You can now chat with the selected bundle; it receives prompts + attachments and replies asynchronously in the UI.
