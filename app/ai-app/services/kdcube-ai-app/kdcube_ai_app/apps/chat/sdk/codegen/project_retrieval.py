# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# apps/chat/sdk/codegen/project_retrieval.py

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional
import re

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    CITATION_OPTIONAL_ATTRS,
    normalize_citation_item,
    normalize_url,
)

# ---- tiny utils ----

def _first_md_heading(md: str) -> str:
    for ln in (md or "").splitlines():
        t = ln.strip()
        if t.startswith("#"):
            return t.lstrip("# ").strip()
    return ""

def _short(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"

# EDITABLE_SLOT_NAMES = {
#     "editable_md","document_md","draft_md","summary_md","outline_md",
#     "body_md","report_md","article_md","content_md","project_canvas", "project_log"
# }

CANVAS_SLOTS = { "project_canvas" }
PROJECT_LOG_SLOTS = { "project_log" }

def _is_markdown_mime(m: Optional[str]) -> bool:
    m = (m or "").lower().strip()
    return m in ("text/markdown", "text/x-markdown", "text/md", "markdown")

def _looks_like_markdown(txt: str) -> bool:
    t = (txt or "").strip()
    return ("```" in t) or t.startswith("#") or "\n#" in t

def _is_markdown_from_format_or_text(fmt: Optional[str], mime: Optional[str], txt: str) -> bool:
    f = (fmt or "").lower().strip()
    if f == "markdown":
        return True
    if _is_markdown_mime(mime):
        return True
    return _looks_like_markdown(txt)

def _pick_project_log_slot(d_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:

    matches = [ d for d in d_items if d.get("slot") in PROJECT_LOG_SLOTS ]
    for m in matches:
        v = m.get("value") or {}
        txt = v.get("value") or v.get("value_preview") or ""
        if not txt:
            continue
        fmt  = (v.get("format") or "").lower()
        return { "slot": m, "format": fmt or "markdown", "value": txt }

def _history_digest(history: list[dict], limit: int = 3) -> str:
    rows = []
    for h in history[:limit]:
        try:
            exec_id, inner = next(iter(h.items()))
        except Exception:
            continue
        inner = inner or {}
        ts = (inner.get("ts") or "")[:10]
        run = inner.get("codegen_run_id") or exec_id or "?"
        # Prefer project_log if present
        log_txt = ""
        try:
            # project_log is saved as a text inline deliverable or as a string in out_dyn
            log_txt = (inner.get("project_log") or {}).get("text") or ""
        except Exception:
            log_txt = ""
        if not log_txt:
            # fallback to headings from canvas/presentation
            pres = inner.get("program_presentation") or inner.get("solver_failure") or ""
            # canvas_txt = ((inner.get("project_canvas") or {}).get("text") or "")
            # title = _first_md_heading(pres) or _first_md_heading(canvas_txt) or _short(canvas_txt, 60) or "(no title)"
            title = _first_md_heading(pres) or "(no title)"
            rows.append(f"{ts} — {title} [run:{run}]")
        else:
            rows.append(f"{ts} — { _short(log_txt, 800) } [run:{run}]")
    return "; ".join(rows) if rows else "none"


async def _build_program_history_from_turn_ids(self, *,
                                               turn_ids: List[str],
                                               scope: str = "track", days: int = 365) -> List[Dict[str, Any]]:
    """
    For each turn_id, materialize: program presentation (if present), project_canvas / project_log
    from deliverables, and citations tied to the run. Returns the same shape as _build_program_history().
    """
    if not self.context_rag_client:
        return []
    out = []
    seen_runs = set()

    for tid in turn_ids:
        mat = await self.context_rag_client.materialize_turn(
            turn_id=tid, scope=scope, days=days, with_payload=True
        )

        # Unpack rich envelopes (payload + ts + tags)
        prez_env = (mat.get("presentation") or {})
        dels_env = mat.get("deliverables") or {}
        assistant_env = mat.get("assistant") or {}
        user_env = mat.get("user") or {}
        solver_failure_env = mat.get("solver_failure") or {}
        citables_env = mat.get("citables") or {}
        files_env = mat.get("files") or {}

        prez = ((prez_env or {}).get("payload") or {}).get("payload") or {}
        dels = ((dels_env or {}).get("payload") or {}).get("payload") or {}
        citables = ((citables_env or {}).get("payload") or {}).get("payload") or {}
        assistant = ((((assistant_env or {}).get("payload") or {}).get("payload") or {})).get("completion") or ""
        user = ((user_env or {}).get("payload") or {}).get("payload") or {}

        files = (((files_env or {}).get("payload") or {}).get("payload") or {}).get("files_by_slot", {})

        d_items = list((dels or {}).get("items") or [])
        for de in d_items:
            de = de or {}
            artifact = de.get("value") or {}
            if artifact.get("type") == "file":
                slot_name = de.get("slot") or "default"
                file = files.get(slot_name) or {}
                artifact["path"] = file.get("path") or ""
                artifact["filename"] = file.get("filename")
                print()

        cite_items =  list((citables or {}).get("items") or [])
        round_reason = (dels or {}).get("round_reasoning") or ""

        # Prefer assistant ts, else user ts
        ts_val = assistant_env.get("ts") or user_env.get("ts") or ""

        # codegen_run_id priority: deliverables.payload -> tags -> presentation markdown
        codegen_run_id = (dels or {}).get("execution_id") or ""

        # Presentation markdown (if present)
        pres_md = (prez.get("markdown") or "") if isinstance(prez, dict) else ""

        # Citations bundle (if we have run id)
        cites = {"items": cite_items}

        # Extract canvas/log from deliverables items
        # canvas = _pick_canvas_slot(d_items) or {}
        project_log = _pick_project_log_slot(d_items) or {}
        materialized_canvas = {}
        try:
            glue = project_log.get("value","") if project_log else ""
            mat = _materialize_glue_canvas(glue, d_items)
            if mat and mat != glue:
                materialized_canvas = {"format": "markdown", "text": mat}
        except Exception as ex:
            materialized_canvas = {}

        exec_id = codegen_run_id
        if exec_id in seen_runs:
            continue
        seen_runs.add(exec_id)

        # Solver failure (markdown, if any)
        solver_failure = ((solver_failure_env or {}).get("payload") or {}).get("payload") or {}
        solver_failure_md = (solver_failure.get("markdown") or "") if isinstance(solver_failure, dict) else ""

        ret = {
            **({"program_presentation": pres_md} if pres_md else {}),
            # **({"project_canvas": {"format": canvas.get("format","markdown"), "text": canvas.get("value","")}} if canvas else {}),
            **({"project_log": {"format": project_log.get("format","markdown"), "text": project_log.get("value","")}} if project_log else {}),
            **({"project_log_materialized": materialized_canvas} if materialized_canvas else {}),
            **({"solver_failure": solver_failure_md} if solver_failure_md else {}),
            **({"web_links_citations": {"items": [normalize_citation_item(c) for c in cites["items"] if normalize_citation_item(c)]}}),
            **{"media": []},
            "ts": ts_val,
            **({"codegen_run_id": codegen_run_id} if codegen_run_id else {}),
            **({"round_reasoning": round_reason} if round_reason else {}),
            "assistant": assistant,
            "user": user,
            "deliverables": d_items if d_items else []
        }
        out.append({exec_id: ret})

    # newest first
    out.sort(key=lambda e: next(iter(e.values())).get("ts","") or "", reverse=True)
    return out

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
_UTM_PARAMS = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","utm_id","gclid","fbclid"}

_CITABLE_TOOL_IDS = {
    "generic_tools.web_search", "generic_tools.browsing",
    "ctx_tools.merge_sources",

}

def _extract_citable_items_from_out_items(out_items: list[dict]) -> list[dict]:
    rows = []
    for r in (out_items or []):
        if not isinstance(r, dict):
            continue
        if r.get("type") != "inline" or not bool(r.get("citable")):
            continue
        tid = (r.get("tool_id") or "").lower()
        if not (tid in _CITABLE_TOOL_IDS or tid.endswith(".kb_search") or tid.endswith(".kb_search_advanced")):
            continue
        out = r.get("output")
        pack = out if isinstance(out, list) else ([out] if isinstance(out, dict) else [])
        for c in pack:
            if isinstance(c, dict):
                url = normalize_url(str(c.get("url") or c.get("href") or ""))
                if not url:
                    continue
                item = {
                    "url": url,
                    "title": c.get("title") or c.get("description") or url,
                    "text": c.get("text") or c.get("body") or c.get("content") or "",
                    "sid": c.get("sid"),
                    "_tool_id": r.get("tool_id") or "",
                    "_resource_id": r.get("resource_id") or "",
                }
                for k in CITATION_OPTIONAL_ATTRS:
                    if c.get(k):
                        item[k] = c[k]
                rows.append(item)
    return rows

def _latest_merge_sources_row(out_items: list[dict]) -> list[dict]:
    """Return the output list from the latest ctx_tools.merge_sources (if any)."""
    import re
    latest_idx, latest_row = -1, None
    for r in (out_items or []):
        if not (isinstance(r, dict) and r.get("type") == "inline" and r.get("citable") is True):
            continue
        if (r.get("tool_id") or "") != "ctx_tools.merge_sources":
            continue
        rid = str(r.get("resource_id") or "")
        m = re.search(r":(\d+)$", rid)
        idx = int(m.group(1)) if m else -1
        if idx > latest_idx:
            latest_idx, latest_row = idx, r
    if latest_row and isinstance(latest_row.get("output"), list):
        return latest_row["output"]
    return []

def _rewrite_md_citation_tokens(md: str, sid_map: dict[int,int]) -> str:
    """
    Replace [[S:1,2]] with [[S:a,b]] using sid_map.
    If none of the numbers in a token map, drop the token entirely.
    """
    if not md or not sid_map:
        return md or ""

    def repl(m):
        body = m.group(1)
        nums = []
        for p in body.split(","):
            p = p.strip()
            if not p.isdigit():
                continue
            old = int(p)
            new = sid_map.get(old)
            if new:
                nums.append(str(new))
        if not nums:
            return ""  # drop token
        return f"[[S:{','.join(nums)}]]"

    return re.sub(r"\[\[S:([0-9,\s]+)\]\]", repl, md)

def reconcile_citations_for_context(history: list[dict], *, max_sources: int = 60, rewrite_tokens_in_place: bool = True):
    """
    Input: output of _build_program_history_from_turn_ids (a list of {exec_id: {...}} sorted newest first).
    Output:
      {
        "canonical_sources": [ { sid, url, title, text } ... ],
        "sid_maps": { run_id -> { old_sid -> sid } }
      }
    Side-effect (optional): rewrites [[S:n]] in project_log AND all deliverables with sources_used,
    and updates web_links_citations per turn to match canonical sources.
    """
    # 1-4: Build canonical sources and sid_maps (existing logic)
    flat: list[tuple[str, dict, dict]] = []
    per_run_rows: dict[str, list[dict]] = {}
    per_run_out_items: dict[str, list[dict]] = {}

    for rec in (history or []):
        run_id, meta = next(iter(rec.items()))
        per_run_out_items[run_id] = meta.get("out_items") or []

    # Synthesize out_items from web_links_citations for extraction
    for rec in (history or []):
        run_id, meta = next(iter(rec.items()))
        citations_list = ((meta.get("web_links_citations") or {}).get("items") or [])
        faux_out_item = {
            "type": "inline",
            "citable": True,
            "tool_id": "ctx_tools.merge_sources",
            "resource_id": f"tool:ctx_tools.merge_sources:0",
            "output": citations_list
        }
        out_items = [faux_out_item]
        per_run_out_items[run_id] = out_items

        ext = _extract_citable_items_from_out_items(out_items)
        per_run_rows[run_id] = ext
        for it in ext:
            flat.append((run_id, meta, it))

    # 2) Determine base ordering
    latest_run_id = next(iter(history[0].keys())) if history else None
    base_order_urls: list[str] = []
    if latest_run_id:
        base_merge = _latest_merge_sources_row(per_run_out_items.get(latest_run_id, []))
        if base_merge:
            base_order_urls = [normalize_url(c.get("url") or "") for c in base_merge if isinstance(c, dict)]
            base_order_urls = [u for u in base_order_urls if u]

    # 3) Build canonical map
    by_url: dict[str, dict] = {}
    ordered_urls: list[str] = []

    # for u in base_order_urls:
    #     if u not in by_url:
    #         by_url[u] = {}
    #         ordered_urls.append(u)

    for run_id, meta, it in flat:
        u = it["url"]
        if u not in by_url:
            by_url[u] = {
                "url": u,
                "title": it["title"],
                "text": it.get("text",""),
            }
            for k in CITATION_OPTIONAL_ATTRS:
                if it.get(k):
                    by_url[u][k] = it[k]
            ordered_urls.append(u)

    ordered_urls = ordered_urls[:max_sources]
    global_sid_of_url: dict[str,int] = {u: i+1 for i,u in enumerate(ordered_urls)}

    canonical_sources = []
    for u in ordered_urls:
        src = by_url[u]
        row = {
            "sid": global_sid_of_url[u],
            "url": u,
            "title": src.get("title") or u,
            "text": src.get("text",""),
        }
        for k in CITATION_OPTIONAL_ATTRS:
            if src.get(k):
                row[k] = src[k]
        canonical_sources.append(row)

    # 4) Build per-run sid maps (old → global)
    sid_maps: dict[str, dict[int,int]] = {}
    for run_id, rows in per_run_rows.items():
        m: dict[int,int] = {}
        for it in rows:
            u = it["url"]
            old_sid = it.get("sid")
            if old_sid is None:
                continue
            try:
                old_sid = int(old_sid)
            except Exception:
                continue
            new_sid = global_sid_of_url.get(u)
            if new_sid:
                m[old_sid] = new_sid
        if m:
            sid_maps[run_id] = m

    # 5) Rewrite tokens and update web_links_citations
    if rewrite_tokens_in_place:
        from kdcube_ai_app.apps.chat.sdk.tools.citations import sids_in_text

        # Quick lookup by SID
        canonical_by_sid = {s["sid"]: s for s in canonical_sources}

        for rec in history:
            run_id, meta = next(iter(rec.items()))
            sid_map = sid_maps.get(run_id, {})
            if not sid_map:
                continue

            # Track SIDs used in this turn
            used_sids_in_turn = set()

            # Rewrite project_log
            if "project_log" in meta and isinstance(meta["project_log"], dict):
                val = meta["project_log"].get("text") or meta["project_log"].get("value") or ""
                if val:
                    new_val = _rewrite_md_citation_tokens(val, sid_map)
                    if "text" in meta["project_log"]:
                        meta["project_log"]["text"] = new_val
                    else:
                        meta["project_log"]["value"] = new_val
                    # Collect SIDs from project_log
                    used_sids_in_turn.update(sids_in_text(new_val))

            # Rewrite all deliverables
            deliverables = meta.get("deliverables") or []
            for d in deliverables:
                if not isinstance(d, dict):
                    continue

                artifact = d.get("value")
                if not isinstance(artifact, dict):
                    continue

                # Check if this deliverable uses sources
                sources_used = artifact.get("sources_used")
                if not sources_used:
                    continue

                # Find text to rewrite
                text = artifact.get("text") or ""
                if text:
                    new_text = _rewrite_md_citation_tokens(text, sid_map)
                    artifact["text"] = new_text
                    used_sids_in_turn.update(sids_in_text(new_text))
                # output = artifact.get("output") or {}
                # text = None
                # text_key = None
                # text_location = None
                #
                # if "text" in output:
                #     text = output["text"]
                #     text_key = "text"
                #     text_location = output
                # elif "value" in artifact:
                #     text = artifact["value"]
                #     text_key = "value"
                #     text_location = artifact

                # if text and isinstance(text, str) and text_key and text_location:
                #     new_text = _rewrite_md_citation_tokens(text, sid_map)
                #     text_location[text_key] = new_text
                #     # Collect SIDs from this deliverable
                #     used_sids_in_turn.update(sids_in_text(new_text))

            # ===== UPDATE web_links_citations =====
            # Replace with canonical sources that are actually used in this turn
            if used_sids_in_turn:
                turn_sources = [
                    canonical_by_sid[sid]
                    for sid in sorted(used_sids_in_turn)
                    if sid in canonical_by_sid
                ]
                meta["web_links_citations"] = {"items": turn_sources}
            else:
                # No citations used in this turn
                meta["web_links_citations"] = {"items": []}

    return {
        "canonical_sources": canonical_sources,
        "sid_maps": sid_maps,
    }


def _short_with_count(text: str, limit: int) -> str:
    """Truncate text and show how much was cut."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    remaining = len(text) - limit
    return f"{text[:limit]}... (...{remaining} more chars)"


def build_program_playbook(history: list[dict], *, max_turns: int = 5) -> str:
    """
    Build a compact, scannable playbook showing what artifacts exist across recent turns.

    Purpose: Help codegen LLM understand:
    - What artifacts exist in history
    - Which turn_ids to fetch for specific content
    - How large artifacts are (to decide if fetch is needed)

    Format:
      Turn <turn_id> — <timestamp> [CURRENT] / [HISTORICAL]
      User Request: <preview>
      Program Log: <preview with size indicator>
      Deliverables:
        • slot_name (type: format/mime) - Size: N chars
          Description: ...
          Preview: [first 100 chars...] (...N more chars)
          ⚠ Use fetch_turn_artifacts(["turn_id"]) to retrieve full content
      Sources: (numbered list with titles and abbreviated links)
    """
    if not history:
        return "(no program history)"

    sections = []

    for idx, rec in enumerate(history[:max_turns]):
        try:
            exec_id, meta = next(iter(rec.items()))
        except Exception:
            continue

        is_current = (idx == 0)  # First entry is always current turn
        turn_label = "CURRENT TURN" if is_current else "HISTORICAL"

        # Timestamp with minutes: 2025-10-02T16:55:45Z -> 2025-10-02 16:55
        ts_full = meta.get("ts") or ""
        ts = ts_full[:16].replace("T", " ") if len(ts_full) >= 16 else (ts_full[:10] or "(no date)")

        # User request (especially important for current turn)
        user_text = ((meta.get("user") or {}).get("prompt") or "").strip()
        user_preview = _short_with_count(user_text, 200) if user_text else "(no user message)"

        # Program log
        pl = (meta.get("project_log") or {})
        pl_text = (pl.get("text") or pl.get("value") or "").strip()
        pl_size = len(pl_text) if pl_text else 0
        pl_preview = _short_with_count(pl_text, 300) if pl_text else "(no log yet)"

        # Sources (global for this turn)
        sources = ((meta.get("web_links_citations") or {}).get("items")) or []
        sources_lines = []
        for i, src in enumerate(sources[:20], 1):  # limit to 20
            if not isinstance(src, dict):
                continue
            title = (src.get("title") or "").strip()
            url = (src.get("url") or "").strip()
            if not title and not url:
                continue

            # Abbreviated URL (domain only)
            domain = ""
            if url:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    domain = parsed.netloc or ""
                except:
                    domain = url[:30]

            if title and domain:
                sources_lines.append(f"  {i}. {_short_with_count(title, 80)} ({domain})")
            elif title:
                sources_lines.append(f"  {i}. {_short_with_count(title, 80)}")
            elif url:
                sources_lines.append(f"  {i}. {_short_with_count(url, 100)}")

        # Deliverables
        deliverables = meta.get("deliverables") or []
        del_lines = []

        for d in deliverables:
            slot_name = d.get("slot") or "(unnamed)"
            if slot_name in {"project_log", "project_canvas"}:
                continue  # Already shown above

            artifact = d.get("value") or {}
            slot_type = artifact.get("type") or "inline"
            desc = d.get("description") or "(no description)"

            # Get text preview and size
            output = artifact.get("output") or {}
            text = output.get("text") or artifact.get("value") or ""
            if isinstance(text, dict):
                text = str(text)
            else:
                text = str(text or "")

            text_size = len(text)
            text_preview = _short_with_count(text, 150) if text else "[empty]"

            # Format with size info
            if slot_type == "file":
                mime = artifact.get("mime") or "unknown"
                filename = output.get("filename") or output.get("path") or "(no filename)"
                del_lines.append(f"  • {slot_name} (file: {mime})")
                del_lines.append(f"    Filename: {filename}")
                del_lines.append(f"    Size: {text_size:,} chars")
            else:
                fmt = artifact.get("format") or "text"
                del_lines.append(f"  • {slot_name} (inline: {fmt})")
                del_lines.append(f"    Size: {text_size:,} chars")

            del_lines.append(f"    Description: {desc}")

            if text:
                del_lines.append(f"    Preview: {text_preview}")

            # Add fetch instruction if content is substantial
            if text_size > 300:
                del_lines.append(f"    ⚠ Full content available via fetch_turn_artifacts([\"{exec_id}\"])")

        # Build section header
        section = [
            f"## Turn {exec_id} — {ts} [{turn_label}]",
            "",
        ]

        # User request (important context)
        section.extend([
            "**User Request:**",
            user_preview,
            "",
        ])

        # Program log
        if pl_text:
            section.extend([
                f"**Program Log:** ({pl_size:,} chars)",
                pl_preview,
                "",
            ])
            if pl_size > 300:
                section.append(f"⚠ Full log via fetch_turn_artifacts([\"{exec_id}\"])")
                section.append("")

        # Deliverables
        if del_lines:
            section.append("**Deliverables:**")
            section.extend(del_lines)
            section.append("")
        else:
            section.append("*No deliverables yet*" if is_current else "*No deliverables*")
            section.append("")

        # Sources
        if sources_lines:
            section.append(f"**Sources:** ({len(sources)} total)")
            section.extend(sources_lines)
            section.append("")

        sections.append("\n".join(section))

    if not sections:
        return "(no program history)"

    header = [
        "# Program History Playbook",
        "",
        f"Showing {len(sections)} turn(s), newest first.",
        "Previews are truncated. Use fetch_turn_artifacts([turn_ids]) for full content.",
        "",
        "---",
        "",
    ]

    return "\n".join(header + sections)

_TEXT_MIMES = {
    "text/plain", "text/markdown", "text/x-markdown", "text/html", "text/css",
    "text/csv", "text/tab-separated-values", "text/xml",
    "application/json", "application/xml",
    "application/yaml", "application/x-yaml",
    "application/javascript", "application/x-javascript",
    "application/x-python", "text/x-python",
    "application/sql", "text/x-sql",
}

def _is_text_mime(m: str | None) -> bool:
    m = (m or "").lower().strip()
    if m in _TEXT_MIMES:
        return True
    return m.startswith("text/")

def _unique_target(base_dir: pathlib.Path, basename: str) -> pathlib.Path:
    """
    Ensure we don't overwrite duplicates; add -1, -2, ... if needed.
    """
    candidate = base_dir / basename
    if not candidate.exists():
        return candidate
    stem = pathlib.Path(basename).stem
    suf  = pathlib.Path(basename).suffix
    i = 1
    while True:
        c = base_dir / f"{stem}-{i}{suf}"
        if not c.exists():
            return c
        i += 1

async def _rehost_previous_files(prev_files: list[dict], workdir: pathlib.Path) -> list[dict]:
    """
    Copy readable (text) files from conversation storage into workdir/files/,
    update each file dict's 'path' to the new on-disk location,
    and annotate with {rehosted: bool, source_path: str}.
    Non-text files are passed through unchanged (rehosted: False).
    """
    from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    out: list[dict] = []
    if not prev_files:
        return out

    files_dir = workdir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    store = ConversationStore(get_settings().STORAGE_PATH)

    for file in prev_files:
        try:
            artifact = file.get("value") or {}
            # output = artifact.get("output") or {}
            mime = artifact.get("mime") or ""
            # src_path = output.get("path") or ""
            src_path = artifact.get("path") or ""
            if not src_path:
                # Nothing to rehost; pass through
                out.append({**artifact, "rehosted": False})
                continue

            if _is_text_mime(mime):
                # Read from conversation storage and write into workdir/files
                # src_path stored is RELATIVE within conversation store
                try:
                    content = store.backend.read_text(src_path)
                except Exception:
                    # If we can't read it, pass through
                    out.append({**artifact, "rehosted": False, "rehost_error": "read_failed"})
                    continue

                basename = pathlib.Path(src_path).name
                target = _unique_target(files_dir, basename)
                target.write_text(content, encoding="utf-8")
                artifact["source_path"] = src_path
                artifact["path"] = str(target)
                artifact["rehosted"] = True
            else:
                # Not a text mime: leave as-is (we're not handling binary copy here)
                artifact["rehosted"] = False
        except Exception as e:
            pass

    return out

def _collect_same_turn_file_texts(d_items: list[dict]) -> list[tuple[str, str]]:
    out = []
    for it in d_items or []:
        if (it or {}).get("type") != "file":
            continue
        # slot name
        rid = str((it or {}).get("resource_id") or "")
        slot = rid.split(":", 1)[1] if rid.startswith("slot:") else (it.get("slot") or "")
        txt = (it or {}).get("text") or ""
        if slot and isinstance(txt, str) and txt.strip():
            out.append((slot, txt.strip()))
    return out

def _materialize_glue_canvas(glue_md: str, d_items: list[dict]) -> str:
    if not (glue_md or "").strip():
        return glue_md or ""
    lines = [glue_md.strip(), "", "## Materials (this turn)", ""]
    for slot in d_items:
        description = slot.get("description") or ""
        slot_name = slot.get("slot") or ""
        artifact = slot.get("value") or {}
        # output = artifact.get("output") or {}
        # text = output.get("text") or ""  # snippet = text[:2000] + ("…" if len(text) > 2000 else "")
        text = artifact.get("text") or ""
        slot_type = artifact.get("type") or "inline"
        lines += [f"### `{slot_name} ({slot_type})`", f"#### Description: {description}", text, ""]
    return "\n".join(lines).strip()

def _compose_last_materialized_canvas_block(history: list[dict]) -> str:
    """
    Return a compact, self-sufficient block that solvability can read.
    Prefers materialized canvas; falls back to raw canvas, then program presentation.
    """
    if not history:
        return "(no prior log)"

    try:
        run_id, meta = next(iter(history[0].items()))
    except Exception:
        return "(no prior log)"

    # 1) Prefer materialized canvas
    mat = (meta.get("project_log_materialized") or {})
    txt = (mat.get("text") or "").strip()
    if txt:
        return f"# Project Canvas (materialized)\n\n{txt}"

    # 2) Fallback to non-materialized canvas
    raw = (meta.get("project_log") or {})
    txt = (raw.get("text") or raw.get("value") or "").strip()
    if txt:
        return f"# Project Canvas\n\n{txt}"

    # 3) Fallback to last program presentation
    prez = (meta.get("program_presentation") or "").strip()
    if prez:
        return f"# Program Presentation (fallback)\n\n{prez}"

    # 4) Nothing available
    return "(no prior canvas)"
