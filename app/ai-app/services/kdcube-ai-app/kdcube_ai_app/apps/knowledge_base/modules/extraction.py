# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/knowledge_base/modules/extraction.py
from __future__ import annotations

import os, re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Union, Protocol
import base64
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from kdcube_ai_app.apps.knowledge_base.modules.base import ProcessingModule
from kdcube_ai_app.tools.parser import SimpleHtmlParser


# --------- tiny data holders ---------
@dataclass
class AssetEntry:
    storage_filename: str
    content: bytes
    meta: Dict[str, Any] | None = None

@dataclass
class ExtractionEntry:
    index: int
    content_filename: str
    content_bytes: bytes
    metadata: Dict[str, Any]
    assets: Dict[str, List[AssetEntry]]

# --------- strategy interface ---------
class ExtractionStrategy(Protocol):
    def run(self) -> List[ExtractionEntry]: ...

# --------- helpers ---------
def _now_iso() -> str:
    return datetime.now().isoformat()

def _is_html_mime(m: Optional[str]) -> bool:
    return bool(m and "html" in m.lower())

def _find_img_srcs(html: str) -> List[str]:
    try:
        return re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    except Exception:
        return []

def _image_url_assets(html: str) -> List[AssetEntry]:
    assets: List[AssetEntry] = []
    for i, src in enumerate(_find_img_srcs(html)):
        assets.append(AssetEntry(storage_filename=f"img_{i:03d}.url",
                                 content=(src + "\n").encode("utf-8"),
                                 meta={"src": src}))
    return assets

# ---- config defaults (tweakable via stages_config["extraction"]) ----
DEFAULT_DL_OPTS = {
    "download_assets": True,
    "max_images": 64,
    "max_image_bytes": 5 * 1024 * 1024,   # 5 MB
    "timeout_sec": 10,
    "rewrite_src": True,
    "persist_html_copy": True,            # <-- also stash original HTML alongside MD
}

def _safe_filename_from_url(src: str, i: int) -> str:
    # derive a reasonable filename, fall back to index numbering
    p = urlparse(src)
    name = (p.path.rsplit("/", 1)[-1] or "").strip()
    if not name:
        return f"img_{i:03d}.bin"
    # Guard against weird query suffixes
    if "?" in name:
        name = name.split("?")[0]
    return name[:128] or f"img_{i:03d}.bin"

def _to_bytes(maybe_bytes_or_base64) -> bytes:
    if isinstance(maybe_bytes_or_base64, bytes):
        return maybe_bytes_or_base64
    if isinstance(maybe_bytes_or_base64, str):
        # try base64, else treat as utf-8 bytes (rare for images)
        try:
            return base64.b64decode(maybe_bytes_or_base64, validate=False)
        except Exception:
            return maybe_bytes_or_base64.encode("utf-8")
    return b""

def _download_image(url: str, timeout: int) -> tuple[bytes, str]:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "")
    return r.content, ctype

def _collect_images_from_html(
        html: str,
        *,
        base_url: Optional[str],
        pre_supplied: Optional[List[Dict]] = None,
        opts: Dict
) -> tuple[str, Dict[str, List[AssetEntry]]]:
    """
    Returns (rewritten_html, assets{"images":[AssetEntry,...]})
    """
    soup = BeautifulSoup(html, "html.parser")
    img_tags = soup.find_all("img")

    # index by original resolved URL to avoid duplicate downloads
    resolved_map: Dict[str, str] = {}  # resolved_url -> storage_filename
    assets: List[AssetEntry] = []

    # Build quick lookup for pre-supplied images by resolved src
    supplied_lookup: Dict[str, Dict] = {}
    if pre_supplied:
        for it in pre_supplied:
            src = it.get("src") or ""
            resolved = urljoin(base_url, src) if base_url else src
            supplied_lookup[resolved] = it

    max_images = int(opts["max_images"])
    timeout = int(opts["timeout_sec"])
    max_bytes = int(opts["max_image_bytes"])
    allow_download = bool(opts["download_assets"])
    rewrite = bool(opts["rewrite_src"])

    count = 0
    for tag in img_tags:
        src = tag.get("src", "").strip()
        if not src:
            continue
        resolved = urljoin(base_url, src) if base_url else src
        if resolved in resolved_map:
            # already handled; just rewrite if requested
            if rewrite:
                tag["src"] = resolved_map[resolved]
            continue
        if count >= max_images:
            continue

        # try pre-supplied first
        blob: bytes = b""
        ctype = None
        desired_name = None
        supplied = supplied_lookup.get(resolved)

        if supplied and supplied.get("content") is not None:
            blob = _to_bytes(supplied["content"])
            ctype = supplied.get("content_type")
            desired_name = supplied.get("filename")

        # else download if allowed and http(s)
        if not blob and allow_download:
            if resolved.lower().startswith(("http://", "https://")):
                try:
                    blob, ctype = _download_image(resolved, timeout)
                except Exception:
                    blob, ctype = b"", None

        if not blob:
            # Skip but keep original src if we can't fetch
            continue
        if len(blob) > max_bytes:
            continue

        # choose filename
        i = count
        filename = desired_name or _safe_filename_from_url(resolved, i)
        # very small guard to avoid path components
        filename = filename.replace("/", "_").replace("\\", "_")

        assets.append(AssetEntry(storage_filename=filename, content=blob, meta={
            "original_src": resolved, "content_type": ctype
        }))
        resolved_map[resolved] = filename
        count += 1

        if rewrite:
            tag["src"] = filename

    rewritten_html = str(soup) if rewrite else html
    return rewritten_html, {"images": assets}

# --------- strategies ---------
class ExternalStrategy:
    def __init__(self, external, *, base_url: Optional[str] = None, opts: Dict = None):

        self.external = external
        self.base_url = base_url

        self.opts = {**DEFAULT_DL_OPTS, **(opts or {})}

    def run(self) -> List[ExtractionEntry]:
        def build_from_html(idx: int, html: str, meta: Dict, pre_images: Optional[List[Dict]] = None):
            rewritten, assets = _collect_images_from_html(
                html, base_url=self.base_url, pre_supplied=pre_images, opts=self.opts
            )
            return ExtractionEntry(
                index=idx,
                content_filename="extraction_0.html",
                content_bytes=rewritten.encode("utf-8"),
                metadata={"mime": "text/html", **(meta or {})},
                assets=assets
            )

        if isinstance(self.external, (str, bytes)):
            html = self.external.decode("utf-8", "ignore") if isinstance(self.external, bytes) else self.external
            return [build_from_html(0, html, meta={})]

        if isinstance(self.external, dict):
            # accept HtmlPostPayload-like dict
            if self.external.get("type") == "html_post":
                html = self.external.get("html") or ""
                base_url = self.external.get("base_url") or self.base_url
                if base_url:
                    self.base_url = base_url
                pre_images = self.external.get("images") or None
                return [build_from_html(0, html, meta=self.external.get("metadata") or {}, pre_images=pre_images)]
            # fallback generic dict: same keys as before
            html = self.external.get("content") or ""
            meta = self.external.get("metadata") or {}
            pre_images = (self.external.get("images") or None)
            return [build_from_html(0, html, meta=meta, pre_images=pre_images)]

        if isinstance(self.external, list):
            out = []
            for i, it in enumerate(self.external):
                if isinstance(it, (str, bytes)):
                    html = it.decode("utf-8", "ignore") if isinstance(it, bytes) else it
                    out.append(build_from_html(i, html, meta={}))
                elif isinstance(it, dict):
                    html = it.get("html") or it.get("content") or ""
                    pre_images = it.get("images") or None
                    out.append(build_from_html(i, html, meta=it.get("metadata") or {}, pre_images=pre_images))
                else:
                    raise ValueError("Unsupported external_extraction item type")
            return out

        raise ValueError("Unsupported external_extraction type")

class HtmlPassThroughStrategy:
    def __init__(self, storage, resource_id: str, version: str, raw_filename: Optional[str],
                 data_element=None, *, base_url: Optional[str] = None, opts: Dict = DEFAULT_DL_OPTS):
        self.storage = storage
        self.resource_id = resource_id
        self.version = version
        self.raw_filename = raw_filename
        self.data_element = data_element
        self.base_url = base_url
        self.opts = {**DEFAULT_DL_OPTS, **(opts or {})}

    def run(self) -> List[ExtractionEntry]:
        raw_bytes: Optional[bytes] = None
        mime: str = "text/html"

        if self.data_element and _is_html_mime(getattr(self.data_element, "mime", None)) and self.data_element.content:
            raw_bytes = self.data_element.content.encode("utf-8") if isinstance(self.data_element.content, str) \
                else self.data_element.content
            self.base_url = self.base_url or getattr(self.data_element, "url", None)

        if raw_bytes is None and self.raw_filename:
            raw_bytes = self.storage.get_stage_content("raw", self.resource_id, self.version,
                                                       self.raw_filename, as_text=False)

        if not raw_bytes:
            raise ValueError("HTML pass-through requested but no RAW HTML found")

        html_text = raw_bytes.decode("utf-8", errors="ignore")
        rewritten, assets = _collect_images_from_html(
            html_text, base_url=self.base_url, pre_supplied=None, opts=self.opts
        )

        return [ExtractionEntry(
            index=0,
            content_filename="extraction_0.html",
            content_bytes=rewritten.encode("utf-8"),
            metadata={"mime": mime, "source_filename": self.raw_filename},
            assets=assets
        )]

class HtmlToMarkdownStrategy:
    """
    Takes HTML (external payload or dict) -> denoise -> rewrite <img src> to persisted assets -> Markdown.
    Produces `extraction_{i}.md` as the main content. Optionally also keeps the HTML as an extra file.
    """

    def __init__(self, external, *, base_url: Optional[str] = None, opts: Dict = None):
        self.external = external
        self.base_url = base_url
        self.opts = {**DEFAULT_DL_OPTS, **(opts or {})}

    def _one(self, idx: int, html: str, meta: Dict, title: str, pre_images: Optional[List[Dict]] = None) -> ExtractionEntry:
        # 1) collect & persistable assets + rewrite <img src>
        rewritten_html, assets = _collect_images_from_html(
            html, base_url=self.base_url, pre_supplied=pre_images, opts=self.opts
        )

        # 2) denoise + HTML → Markdown
        parser = SimpleHtmlParser()
        md = parser.parse(rewritten_html, self.base_url or "", title=title)

        # 3) optionally store original HTML as an extra asset
        if self.opts.get("persist_html_copy", False):
            assets.setdefault("other", []).append(
                AssetEntry(storage_filename=f"extraction_{idx}.html",
                           content=rewritten_html.encode("utf-8"),
                           meta={"role": "original_html"})
            )

        return ExtractionEntry(
            index=idx,
            content_filename=f"extraction_{idx}.md",
            content_bytes=md.encode("utf-8"),
            metadata={"mime": "text/markdown", **(meta or {})},
            assets=assets
        )

    def run(self) -> List[ExtractionEntry]:
        # Accept string/bytes, dict (HtmlPostPayload-like), or list of either
        if isinstance(self.external, (str, bytes)):
            html = self.external.decode("utf-8", "ignore") if isinstance(self.external, bytes) else self.external
            return [self._one(0, html, meta={}, title="")]

        if isinstance(self.external, dict):
            if self.external.get("type") == "html_post":
                html = self.external.get("html") or ""
                base_url = self.external.get("base_url") or self.base_url
                if base_url:
                    self.base_url = base_url
                pre_images = self.external.get("images") or None
                return [self._one(0, html, meta=self.external.get("metadata") or {}, title=self.external.get("title"), pre_images=pre_images)]
            # generic dict fallback
            html = self.external.get("html") or self.external.get("content") or ""
            pre_images = self.external.get("images") or None
            return [self._one(0, html, meta=self.external.get("metadata") or {}, title=self.external.get("title"), pre_images=pre_images)]

        if isinstance(self.external, list):
            out: List[ExtractionEntry] = []
            for i, it in enumerate(self.external):
                if isinstance(it, (str, bytes)):
                    html = it.decode("utf-8", "ignore") if isinstance(it, bytes) else it
                    out.append(self._one(i, html, meta={}, title=""))
                elif isinstance(it, dict):
                    html = it.get("html") or it.get("content") or ""
                    pre_images = it.get("images") or None
                    title = it.get("title") or ""
                    out.append(self._one(i, html, meta=it.get("metadata") or {}, title=title, pre_images=pre_images))
                else:
                    raise ValueError("Unsupported external_extraction item type for HtmlToMarkdownStrategy")
            return out

        raise ValueError("Unsupported external_extraction type for HtmlToMarkdownStrategy")


class DataSourceDrivenStrategy:
    """Default path (previous behavior): use data_source.extract() → Markdown."""
    def __init__(self, data_source): self.data_source = data_source

    def run(self) -> List[ExtractionEntry]:
        results = self.data_source.extract()
        out: List[ExtractionEntry] = []
        for i, r in enumerate(results):
            b = r.content.encode("utf-8") if isinstance(r.content, str) else r.content
            # normalize assets
            assets_out: Dict[str, List[AssetEntry]] = {}
            for group, lst in (r.metadata.get("assets") or {}).items():
                group_list: List[AssetEntry] = []
                for a in lst or []:
                    if "content" in a and "storage_filename" in a:
                        ab = a["content"].encode("utf-8") if isinstance(a["content"], str) else a["content"]
                        group_list.append(AssetEntry(
                            storage_filename=a["storage_filename"],
                            content=ab,
                            meta={k: v for k, v in a.items() if k not in ("content", "storage_filename")}
                        ))
                assets_out[group] = group_list

            out.append(ExtractionEntry(
                index=i,
                content_filename=f"extraction_{i}.md",
                content_bytes=b,
                metadata={k: v for k, v in r.metadata.items() if k != "assets"},
                assets=assets_out
            ))
        return out

# --------- the module (thin orchestration) ---------
class ExtractionModule(ProcessingModule):
    @property
    def stage_name(self) -> str:
        return "extraction"

    async def process(self, resource_id: str, version: str, force_reprocess: bool = False, **kwargs):
        self.logger.info(f"EXTRACTION. Resource: {resource_id}.{version}")

        if not force_reprocess and self.is_processed(resource_id, version):
            self.logger.info(f"Extraction already exists for {resource_id} v{version}, skipping")
            return self.get_extraction_results(resource_id, version) or []

        entries = self._choose_strategy_and_run(resource_id, version, **kwargs)
        results = self._persist(resource_id, version, entries)
        self.storage.save_extraction_results(resource_id, version, results)
        self.log_operation("extraction_complete", resource_id, {
            "version": version,
            "extraction_count": len(results),
            "total_files": sum(r["total_files"] for r in results),
        })
        return results

    # ----- public helpers -----
    def get_extraction_results(self, resource_id: str, version: str) -> Optional[List[Dict[str, Any]]]:
        return self.storage.get_extraction_results(resource_id, version)

    def get_extraction_content(self, resource_id: str, version: str, extraction_index: int = 0) -> Optional[str]:
        # prefer html, then md, then any
        for fn in (f"extraction_{extraction_index}.html", f"extraction_{extraction_index}.md"):
            txt = self.storage.get_stage_content(self.stage_name, resource_id, version, fn, as_text=True)
            if txt is not None:
                return txt
        files = sorted(self.storage.list_stage_files(self.stage_name, resource_id, version) or [])
        if not files:
            return None
        return self.storage.get_stage_content(self.stage_name, resource_id, version, files[0], as_text=True)

    def list_assets(self, resource_id: str, version: str) -> Dict[str, List[str]]:
        files = self.storage.list_stage_files(self.stage_name, resource_id, version) or []
        out = {"content": [], "images": [], "tables": [], "metadata": [], "other": []}
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}
        table_exts = {".csv", ".tsv", ".xlsx"}
        meta_exts = {".json", ".xml", ".yaml", ".yml"}
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if f.endswith(".html") or f.endswith(".md"):
                out["content"].append(f)
            elif ext in image_exts:
                out["images"].append(f)
            elif ext in table_exts:
                out["tables"].append(f)
            elif ext in meta_exts and not f.startswith("extraction"):
                out["metadata"].append(f)
            else:
                out["other"].append(f)
        return out

    # ----- internals -----
    def _choose_strategy_and_run(self, resource_id: str, version: str, **kwargs) -> List[ExtractionEntry]:
        # prefer converting external HTML → Markdown?
        html_to_markdown = bool(kwargs.get("html_to_markdown", False))

        # external_extraction wins
        if "external_extraction" in kwargs and kwargs["external_extraction"] is not None:
            opts = {**DEFAULT_DL_OPTS, **(kwargs or {})}
            if html_to_markdown:
                return HtmlToMarkdownStrategy(
                    kwargs["external_extraction"],
                    base_url=kwargs.get("base_url"),
                    opts=opts
                ).run()
            # original external pass-through to HTML (with image persistence)
            return ExternalStrategy(kwargs["external_extraction"],
                                    base_url=kwargs.get("base_url"), opts=opts).run()

        # prefer HTML pass-through from RAW (keeps HTML, not MD)
        data_element = kwargs.get("data_element")
        prefer = bool(kwargs.get("prefer_html_passthrough"))
        if not prefer and data_element is not None:
            ingest = getattr(data_element, "ingest", None)
            prefer = bool(getattr(ingest, "prefer_html_passthrough", False))

        if prefer:
            vmeta = self.storage.get_version_metadata(resource_id, version) or {}
            raw_filename = vmeta.get("filename")
            opts = {**DEFAULT_DL_OPTS, **(kwargs or {})}
            return HtmlPassThroughStrategy(self.storage, resource_id, version, raw_filename,
                                           data_element=data_element,
                                           base_url=kwargs.get("base_url"),
                                           opts=opts).run()

        # default: data-source-driven (legacy behavior)
        if not data_element:
            raise ValueError("data_element required for data-source driven extraction")
        return DataSourceDrivenStrategy(data_element.to_data_source()).run()


    def _persist(self, resource_id: str, version: str, entries: List[ExtractionEntry]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for e in entries:
            # content
            self.storage.save_stage_content(self.stage_name, resource_id, version, e.content_filename, e.content_bytes)
            # assets
            stored: Dict[str, List[Dict[str, Any]]] = {}
            for group, lst in (e.assets or {}).items():
                out_group: List[Dict[str, Any]] = []
                for a in lst:
                    try:
                        self.storage.save_stage_content(self.stage_name, resource_id, version, a.storage_filename, a.content)
                        out_group.append({
                            "storage_filename": a.storage_filename,
                            "stored": True,
                            "storage_path": self.storage.get_stage_file_path(self.stage_name, resource_id, version, a.storage_filename),
                            "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{group}:{version}:{a.storage_filename}",
                            **(a.meta or {})
                        })
                    except Exception as ex:
                        out_group.append({"storage_filename": a.storage_filename, "stored": False, "error": str(ex), **(a.meta or {})})
                stored[group] = out_group

            results.append({
                "index": e.index,
                "content_file": e.content_filename,
                "rn": f"ef:{self.tenant}:{self.project}:knowledge_base:{self.stage_name}:{resource_id}:{version}:{e.content_filename}",
                "metadata": e.metadata or {},
                "assets": stored,
                "extraction_timestamp": _now_iso(),
                "total_files": 1 + sum(len(v) for v in stored.values())
            })
        return results

    def get_asset_url(self, resource_id: str, version: str, asset_filename: str) -> Optional[str]:
        """Get the full URL/path to an extraction asset for external access."""
        try:
            return self.storage.get_stage_full_path(self.stage_name, resource_id, version, asset_filename)
        except Exception as e:
            self.logger.error(f"Failed to get URL for extraction asset {asset_filename}: {e}")
            return None

    def get_extraction_stats(self, resource_id: str, version: str) -> Dict[str, Any]:
        """Get statistics about the extraction results."""
        results = self.get_extraction_results(resource_id, version)
        if not results:
            return {}

        stats = {
            "extraction_count": len(results),
            "total_files": sum(r.get("total_files", 0) for r in results),
            "assets_by_type": {},
            "content_files": []
        }

        # Aggregate asset statistics
        for result in results:
            stats["content_files"].append(result.get("content_file"))

            assets = result.get("assets", {})
            for asset_type, asset_list in assets.items():
                if asset_type not in stats["assets_by_type"]:
                    stats["assets_by_type"][asset_type] = 0
                stats["assets_by_type"][asset_type] += len(asset_list)

        return stats

    def delete_extraction_asset(self, resource_id: str, version: str, asset_filename: str) -> bool:
        """Delete a specific extraction asset."""
        try:
            self.storage.delete_stage_content(self.stage_name, resource_id, version, asset_filename)
            self.logger.info(f"Deleted extraction asset: {asset_filename}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete extraction asset {asset_filename}: {e}")
            return False

    def reprocess_assets_only(self, resource_id: str, version: str) -> Dict[str, Any]:
        """Reprocess just the asset storage part of extraction."""
        # Get existing extraction results
        extraction_results = self.get_extraction_results(resource_id, version)
        if not extraction_results:
            raise ValueError(f"No existing extraction results found for {resource_id} v{version}")

        # Re-extract and re-store assets for each result
        updated_results = []
        for result in extraction_results:
            # Get the original extraction content
            content_file = result.get("content_file")
            if content_file:
                content = self.storage.get_stage_content(self.stage_name, resource_id, version, content_file, as_text=True)

                # Here you could re-run asset extraction logic if needed
                # For now, just maintain existing structure
                updated_results.append(result)

        return {"reprocessed_assets": len(updated_results)}

    def get_asset(self, resource_id: str, version: str, asset_filename: str) -> Optional[bytes]:
        """Retrieve a specific extraction asset by filename."""
        try:
            return self.storage.get_stage_content(
                self.stage_name, resource_id, version, asset_filename, as_text=False
            )
        except Exception as e:
            self.logger.error(f"Failed to retrieve extraction asset {asset_filename}: {e}")
            return None
