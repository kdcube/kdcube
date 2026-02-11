# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/safe_preflight.py

from __future__ import annotations
import io, re, asyncio, zipfile, time, os
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from kdcube_ai_app.infra.service_hub.multimodality import MODALITY_IMAGE_MIME, MODALITY_DOC_MIME

# Text-like formats we allow even when mime is not text/*
TEXT_LIKE_MIME = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/css",
    "text/xml",
    "text/yaml",
    "text/x-yaml",
    "text/javascript",
    "application/json",
    "application/x-ndjson",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/javascript",
}


# ---------- Config & Result ----------



@dataclass
class PreflightConfig:
    # AV
    av_scan: bool = False
    av_timeout_s: float = 3.0

    # Policy: generic ZIP archives (non-OOXML)
    allow_zip: bool = False

    # PDF limits
    pdf_max_pages: int = 500
    pdf_max_objects_hint: int = 100_000
    pdf_max_objstm: int = 2_000
    pdf_max_updates: int = 5
    pdf_total_declared_stream_len_max: int = 100 * 1024 * 1024  # best-effort

    # ZIP/OOXML limits
    zip_max_files: int = 2_000
    zip_max_uncompressed_total: int = 120 * 1024 * 1024
    zip_max_ratio: float = 200.0
    zip_disallow_nested_zip: bool = True

    # Text limits
    text_max_bytes: int = 10 * 1024 * 1024

    # OOXML allowlist
    allow_docx: bool = True
    allow_pptx: bool = True
    allow_xlsx: bool = True
    allow_macros: bool = False  # block .docm/.pptm

@dataclass
class PreflightResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    def deny(self, reason: str):
        self.allowed = False
        self.reasons.append(reason)
        return self

# ---------- Utilities ----------

def sniff_magic(data: bytes, filename: str = "") -> str:
    try:
        import magic  # type: ignore
        m = magic.from_buffer(data, mime=True)
        if m:
            return m
    except Exception:
        pass
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data[:2] == b"PK":
        return "application/zip"
    fn = filename.lower()
    if fn.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".gif"):
        return "image/gif"
    if fn.endswith(".webp"):
        return "image/webp"
    if fn.endswith(".txt"):
        return "text/plain"
    if fn.endswith(".md"):
        return "text/markdown"
    if fn.endswith(".json"):
        return "application/json"
    if fn.endswith((".yml", ".yaml")):
        return "application/x-yaml"
    if fn.endswith(".csv"):
        return "text/csv"
    if fn.endswith(".toml"):
        return "application/toml"
    return "application/octet-stream"

# ---------- AV (async) ----------

async def av_scan_bytes_async(data: bytes, timeout_s: float) -> tuple[bool, str]:
    """
    Async ClamAV scan via thread executor. Fail-open on errors/timeout.
    Returns (infected, signature_desc).
    """
    loop = asyncio.get_running_loop()

    def _scan_blocking() -> tuple[bool, str]:
        try:
            import clamd  # type: ignore
            host = os.getenv("CLAMAV_HOST", "127.0.0.1")
            port = int(os.getenv("CLAMAV_PORT", "3310"))
            try:
                cd = clamd.ClamdUnixSocket()
                cd.ping()
            except Exception:
                cd = clamd.ClamdNetworkSocket(host=host, port=port)
                cd.ping()
            start = time.time()
            res = cd.instream(io.BytesIO(data))
            if (time.time() - start) > timeout_s:
                return (False, "")
            status, desc = res.get("stream", ("UNKNOWN", ""))
            return (status == "FOUND"), (desc or "")
        except Exception:
            return (False, "")

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _scan_blocking), timeout=timeout_s + 0.5)
    except Exception:
        return (False, "")

# ---------- PDF preflight ----------

_P_LEN = re.compile(rb"/Length\s+(\d+)")
def preflight_pdf(data: bytes, cfg: PreflightConfig) -> PreflightResult:
    r = PreflightResult(allowed=True, meta={"type": "pdf"})
    if not data.startswith(b"%PDF"):
        return r.deny("Not a PDF")

    updates = data.count(b"startxref")
    if updates > cfg.pdf_max_updates:
        r.deny(f"Too many incremental updates: {updates}>{cfg.pdf_max_updates}")

    obj_hint = data.count(b" obj")
    if obj_hint > cfg.pdf_max_objects_hint:
        r.deny(f"Too many objects (hint): {obj_hint}>{cfg.pdf_max_objects_hint}")

    objstm = data.count(b"/ObjStm")
    if objstm > cfg.pdf_max_objstm:
        r.deny(f"Too many object streams: {objstm}>{cfg.pdf_max_objstm}")

    pages_hint = data.count(b"/Type /Page")
    if pages_hint > cfg.pdf_max_pages:
        r.deny(f"Too many pages (hint): {pages_hint}>{cfg.pdf_max_pages}")

    if len(data) <= 8 * 1024 * 1024:
        total_declared = 0
        for m in _P_LEN.finditer(data):
            try:
                total_declared += int(m.group(1))
                if total_declared > cfg.pdf_total_declared_stream_len_max:
                    r.deny("Declared stream lengths exceed limit")
                    break
            except Exception:
                continue
        r.meta["pdf_total_declared_stream_len"] = total_declared

    r.meta.update({
        "updates": updates,
        "objects_hint": obj_hint,
        "objstm": objstm,
        "pages_hint": pages_hint,
        "bytes": len(data),
    })
    return r

# ---------- ZIP/OOXML preflight ----------

def _is_zip_nested(name: str) -> bool:
    n = name.lower()
    return n.endswith((".zip", ".jar", ".war", ".apk"))

def preflight_zip_ooxml(data: bytes, cfg: PreflightConfig) -> PreflightResult:
    r = PreflightResult(allowed=True, meta={"type": "zip"})
    if data[:2] != b"PK":
        return r.deny("Not a ZIP")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        return r.deny(f"ZIP open failed: {e!r}")

    infos = zf.infolist()
    files = len(infos)
    if files > cfg.zip_max_files:
        return r.deny(f"Too many entries: {files}>{cfg.zip_max_files}")

    total_uncomp = 0
    suspicious_ratio = False
    nested = False
    for zi in infos:
        total_uncomp += zi.file_size
        comp = max(1, zi.compress_size)
        if (zi.file_size / comp) > cfg.zip_max_ratio:
            suspicious_ratio = True
        if cfg.zip_disallow_nested_zip and _is_zip_nested(zi.filename):
            nested = True
        if total_uncomp > cfg.zip_max_uncompressed_total:
            return r.deny("ZIP expands too large")

    if suspicious_ratio:
        r.deny(f"Suspicious compression ratio > {cfg.zip_max_ratio}")
    if nested:
        r.deny("Nested archives not allowed")

    # OOXML policy
    ooxml_kind = None
    try:
        names = set(zf.namelist())
        if "[Content_Types].xml" in names:
            ct = zf.read("[Content_Types].xml").decode("utf-8", "replace")
            if "wordprocessingml.document" in ct:
                ooxml_kind = "docx"
            if "presentationml.presentation" in ct:
                ooxml_kind = "pptx"
            if "spreadsheetml.sheet" in ct:
                ooxml_kind = "xlsx"
            macro = ("macroEnabled" in ct) or (".vbaProject" in "\n".join(names))
            if macro and not cfg.allow_macros:
                r.deny("Macro-enabled OOXML is not allowed")
            if ooxml_kind == "docx" and not cfg.allow_docx:
                r.deny("DOCX not allowed by policy")
            if ooxml_kind == "pptx" and not cfg.allow_pptx:
                r.deny("PPTX not allowed by policy")
            if ooxml_kind == "xlsx" and not cfg.allow_xlsx:
                r.deny("XLSX not allowed by policy")
    except Exception:
        pass

    r.meta.update({"files": files, "total_uncompressed": total_uncomp, "ooxml_kind": ooxml_kind})
    return r

# ---------- Text preflight ----------

def preflight_text(data: bytes, cfg: PreflightConfig) -> PreflightResult:
    r = PreflightResult(allowed=True, meta={"type": "text", "bytes": len(data)})
    if len(data) > cfg.text_max_bytes:
        r.deny(f"Text too large: {len(data)}>{cfg.text_max_bytes}")
    return r

# ---------- Image preflight ----------

def preflight_image(data: bytes, mime: str) -> PreflightResult:
    return PreflightResult(allowed=True, meta={"type": "image", "mime": mime, "bytes": len(data)})

# ---------- Dispatcher (async) ----------

async def preflight_async(
        data: bytes,
        filename: str,
        mime_hint: Optional[str],
        cfg: Optional[PreflightConfig] = None
) -> PreflightResult:
    cfg = cfg or PreflightConfig()
    mime = (mime_hint or "").lower().strip() or sniff_magic(data, filename)
    res = PreflightResult(allowed=True, meta={"mime": mime, "filename": filename})

    if cfg.av_scan:
        infected, sig = await av_scan_bytes_async(data, cfg.av_timeout_s)
        if infected:
            return res.deny(f"AV detected malware: {sig}")

    if mime in MODALITY_DOC_MIME or data.startswith(b"%PDF"):
        r = preflight_pdf(data, cfg)
        res.meta.update(r.meta)
        if not r.allowed:
            res.allowed = False
            res.reasons.extend(r.reasons)
        return res

    if (
            mime in (
            "application/zip",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) or data[:2] == b"PK"
    ):
        r = preflight_zip_ooxml(data, cfg)
        res.meta.update(r.meta)

        # If not OOXML (i.e., a generic archive), enforce policy switch
        if r.meta.get("ooxml_kind") is None and not cfg.allow_zip:
            return res.deny("Archives (ZIP) are disallowed by policy")

        if not r.allowed:
            res.allowed = False
            res.reasons.extend(r.reasons)
        return res

    if mime in MODALITY_IMAGE_MIME:
        r = preflight_image(data, mime)
        res.meta.update(r.meta)
        return res

    if mime in TEXT_LIKE_MIME or mime.startswith("text/"):
        r = preflight_text(data, cfg)
        res.meta.update(r.meta)
        if not r.allowed:
            res.allowed = False
            res.reasons.extend(r.reasons)
        return res

    return res.deny(f"Unsupported or unknown type: {mime}")
