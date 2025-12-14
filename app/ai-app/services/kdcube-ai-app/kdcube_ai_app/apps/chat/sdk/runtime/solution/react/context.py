
# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/apps/chat/sdk/runtime/solution/react/context.py

from __future__ import annotations

import time, json, logging
import pathlib
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Tuple

from kdcube_ai_app.apps.chat.sdk.tools.citations import (
    normalize_sources_any, dedupe_sources_by_url, adapt_source_for_llm
)
import kdcube_ai_app.apps.chat.sdk.tools.tools_insights as tools_insights
from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.strategy_and_budget import BudgetState, format_budget_for_llm

from kdcube_ai_app.apps.chat.sdk.runtime.solution.contracts import SlotSpec
_SUMMARY_MAX = 600

log = logging.getLogger(__name__)

def format_tool_signature(tool_id: str, params: Dict[str, Any], fetch_directives: List[Dict[str, Any]],
                          adapters: List[Dict[str, Any]], *, trim=80) -> str:
    """
    Build call signature like:
      generic_tools.web_search(queries=["..."], objective=<turn_42.slots.digest_md.text>, reconciling=True, n=10)
    Paths injected via fetch_context appear as <path>; multiple paths use " | ".
    Param ordering follows adapter.call_template when available.
    """
    # index call_template
    order: List[str] = []
    template = next((a.get("call_template") for a in adapters if a.get("id") == tool_id), "")
    if "(" in template and ")" in template:
        inner = template.split("(",1)[1].rsplit(")",1)[0]
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        for p in parts:
            # name={$foo$}
            name = p.split("=",1)[0].strip()
            order.append(name)

    # group fetches by param
    fetch_map: Dict[str, List[str]] = {}
    for fd in (fetch_directives or []):
        pn = (fd or {}).get("param_name")
        path = (fd or {}).get("path")
        if pn and path:
            fetch_map.setdefault(pn, []).append(path)

    # params set
    keys = list(dict.fromkeys(order + list(params.keys())))
    segs = []
    for k in keys:
        v_inline = params.get(k, None)
        paths = fetch_map.get(k, [])
        if paths:
            placeholder = " | ".join([f"<{p}>" for p in paths])
            if v_inline is None or v_inline == "" or (isinstance(v_inline, (list,dict)) and not v_inline):
                segs.append(f"{k}={placeholder}")
            else:
                # inline + fetched → show both briefly
                vv = _short_value(v_inline, trim)
                segs.append(f"{k}={vv} + {placeholder}")
        else:
            segs.append(f"{k}={_short_value(v_inline, trim)}")
    return f"{tool_id}({', '.join(segs)})"

def _short_value(v: Any, trim: int) -> str:
    if isinstance(v, str):
        s = v.strip()
        return f"\"{s[:trim]}...\"" if len(s) > trim else json.dumps(s, ensure_ascii=False)
    if isinstance(v, (list, dict)):
        return f"<{type(v).__name__} len={len(v)}>"
    return json.dumps(v, ensure_ascii=False)

def _extract_text_and_sources(obj: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
    """
    Return (surrogate_text_or_inline_content, sources_used) from a tool-result or slot object.

    Canonical artifact shape (we control it):
      obj: {
        "value": {
          # INLINE:
          #   "content": <str>,
          #
          # FILE:
          #   "type": "file",
          #   "text": <str>,           # authoritative surrogate
          #   "path": <str>,           # rendered file path
          #   "filename": <str>,       # rendered file name (optional)
          #
          # Optional (either kind):
          #   "mime": <str>,
          #   "sources_used": [ ... ]
        },
        "summary": <str>,              # optional, legacy fallback
        "sources_used": [ ... ]        # optional, legacy
      }

    Rules:
      - Always read from obj["value"] (authoritative).
      - For FILE artifacts: prefer value["text"].
      - For INLINE artifacts: prefer value["content"].
      - As permissive fallback, accept common authoring keys ("markdown", "html", "json", "yaml") if present.
      - Only if no usable text found in value, fall back to obj["summary"] (last resort).
      - Sources come from value["sources_used"] first, then obj["sources_used"].
    """
    if not isinstance(obj, dict):
        return "", []

    # NEW: slot-shaped artifact (inline/file) at the root
    # Inline may carry 'text' or 'content'; file carries 'text' (surrogate)
    if obj.get("type") in {"inline", "file"}:
        if isinstance(obj.get("text"), str) and obj["text"].strip():
            return obj["text"].strip(), (obj.get("sources_used") or [])
        if isinstance(obj.get("content"), str) and obj["content"].strip():  # historical content key for inline
            return obj["content"].strip(), (obj.get("sources_used") or [])

    v = obj.get("value") or {}
    if not isinstance(v, dict):
        # Shape is guaranteed by us, but keep a safe fallback
        su = obj.get("sources_used") or []
        return "", su if isinstance(su, list) else []

    # --- Surrogate / inline content extraction ---
    txt: Optional[str] = None

    # File artifact surrogate (authoritative for files)
    if isinstance(v.get("text"), str) and v["text"].strip():
        txt = v["text"].strip()

    # Inline artifact content
    if txt is None and isinstance(v.get("content"), str) and v["content"].strip():
        txt = v["content"].strip()

    # --- Sources extraction (prefer value, then parent) ---
    su = v.get("sources_used")
    if not isinstance(su, list) or not su:
        su = obj.get("sources_used") or []
    if not isinstance(su, list):
        su = []

    return txt or "", su

from typing import Dict, Any, Iterable, Optional


def _to_str(value: Any) -> str:
    """
    Best-effort conversion to string for arbitrary values.
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _pick_first(*values: Any) -> Optional[Any]:
    """
    Return the first non-empty / non-None value in values.
    """
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None

# ---------- Data model ----------

@dataclass
class ReactContext:
    """
    Canonical in-memory state for a ReAct session.
    Persisted to <outdir>/context.json after every mutation.
    """
    # Historical materialized turns (immutable during session)
    history_turns: list[Dict] = field(default_factory=list)
    prior_turns: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Current turn artifacts
    current_slots: Dict[str, Dict[str, Any]] = field(default_factory=dict)          # current_turn.slots.<slot_name> -> artifact
    current_tool_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # current_turn.tool_results.<artifact_id> -> artifact
    # Session timeline (chronological, oldest → newest)
    events: List[Dict[str, Any]] = field(default_factory=list)
    tool_call_index: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Canonical sources pool for the *current turn* (stable SIDs)
    sources_pool: List[Dict[str, Any]] = field(default_factory=list)

    # Counters
    max_sid: int = 0
    tool_result_counter: int = 0
    # I/O
    outdir: Optional[pathlib.Path] = None

    # current turn
    timezone: Optional[str] = None
    turn_id: Optional[str] = None
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    bundle_id: Optional[str] = None
    user_text: Optional[str] = None
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    budget_state: BudgetState = field(default_factory=BudgetState)

    # ---------- Persistence ----------
    def bind_storage(self, outdir: pathlib.Path) -> "ReactContext":
        self.outdir = outdir
        self.persist()
        return self

    def _ctx_path(self) -> pathlib.Path:
        if not self.outdir:
            raise RuntimeError("ReactContext.outdir is not bound")
        return self.outdir / "context.json"

    # --- Slot mapping logging helpers ---------------------------------
    def _slot_mapping_status(self, one_map: Dict[str, Any]) -> Dict[str, Any]:
        slot_name = (one_map.get("slot_name") or "").strip()
        source_path = (one_map.get("source_path") or "").strip()
        draft_flag = bool(one_map.get("draft"))
        gaps = one_map.get("gaps")

        artifact = self.current_slots.get(slot_name) if slot_name else None
        artifact = artifact or {}
        art_type = artifact.get("type") or "inline"
        art_path = artifact.get("path") or source_path or ""

        gaps_flag = "none"
        if isinstance(gaps, str) and gaps.strip():
            gaps_flag = "present"

        return {
            "slot_name": slot_name,
            "source_path": source_path,
            "artifact_type": art_type,
            "artifact_path": art_path,
            "draft_flag": draft_flag,
            "gaps_flag": gaps_flag,
            "gaps": gaps
        }

    @staticmethod
    def _format_slot_mapping_log(label: str, status: Dict[str, Any]) -> str:
        return (
            f"[{label}] slot `{status['slot_name']}` ← {status['artifact_path'] or '<?>'} "
            f"(type={status['artifact_type']}, "
            f"draft={'yes' if status['draft_flag'] else 'no'}, "
            f"gaps={status['gaps_flag']})"
        )

    def slot_mapping_trace(self, one_map: Dict[str, Any], *, label: str) -> str:
        status = self._slot_mapping_status(one_map)
        return self._format_slot_mapping_log(label, status)


    def persist(self) -> None:
        """Write the entire session context to context.json."""
        if not self.outdir:
            return

        payload = {
            "prior_turns": self.prior_turns,
            "current_turn": {
                "turn_id": self.turn_id,
                "user": {
                    "prompt": self.user_text,
                },
                "ts": self.started_at,
                "slots": self.current_slots,
                "tool_results": self.current_tool_results,
                "events": self.events,
                "tool_result_counter": self.tool_result_counter,
            },
            "max_sid": self.max_sid,
            "sources_pool": self.sources_pool,
            "last_persisted_at_ts": time.time(),
            "tool_call_index": self.tool_call_index,
        }
        # Snapshot of budget state for debugging / audit
        try:
            if hasattr(self, "budget_state") and self.budget_state is not None:
                payload["budget_state"] = asdict(self.budget_state)
        except Exception:
            pass
        self._ctx_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _surrogate_from_writer_inputs(self, *, tool_id: str, inputs: Dict[str, Any]) -> tuple[str | None, str | None]:
        """
        Return (surrogate_text, mime_hint) extracted from the persisted inputs of a write_* tool.

        Rules:
          - For text-renderers (pdf/pptx/docx/html): prefer 'content'|'markdown'|'html'|'text'
          - For write_file with BYTES: surrogate is 'content_description' (REQUIRED for binary)
          - For write_file with STRING: surrogate is that string
          - MIME: prefer explicit 'mime' input; otherwise derive by tool_id default; otherwise None
        """
        if not isinstance(inputs, dict):
            return None, None

        mime_hint = (inputs.get("mime") or None)
        if tool_id == "generic_tools.write_file":
            content = inputs.get("content")
            if isinstance(content, (bytes, bytearray)):
                cd = inputs.get("content_description")
                return (cd if isinstance(cd, str) and cd.strip() else None), (mime_hint or "application/octet-stream")
            if isinstance(content, str):
                return content, (mime_hint or "application/octet-stream")
            return None, (mime_hint or "application/octet-stream")

        for key in ("content", "markdown", "html", "text"):
            v = inputs.get(key)
            if isinstance(v, str) and v.strip():
                return v, (mime_hint or tools_insights.default_mime_for_write_tool(tool_id))
        return None, (mime_hint or tools_insights.default_mime_for_write_tool(tool_id))

    # ---------- Sources pool seeding ----------
    def seed_sources_pool_from_prior(self) -> None:
        """Collect prior turn sources into current pool; keep SIDs; don’t renumber."""
        acc: List[Dict[str, Any]] = []
        for _tid, turn in (self.prior_turns or {}).items():
            acc.extend(normalize_sources_any((turn or {}).get("sources")))
        if acc:
            # Keep as-is (SIDs pre-reconciled by history)
            self.sources_pool = acc
            try:
                mx = max(int(s.get("sid") or 0) for s in acc if isinstance(s, dict))
                if mx > self.max_sid:
                    self.max_sid = mx
            except Exception:
                pass
            self.persist()

    def remap_sources_to_pool_sids(self, tool_sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return tool_sources with SIDs replaced by those in the pool (match by URL)."""
        if not tool_sources or not self.sources_pool:
            return tool_sources or []
        by_url = { (s.get("url") or "").strip(): int(s.get("sid") or 0) for s in self.sources_pool if (s.get("url") or "").strip() }
        out = []
        for row in normalize_sources_any(tool_sources):
            u = (row.get("url") or "").strip()
            if u and u in by_url:
                row["sid"] = by_url[u]
            out.append(row)
        return out

    # ---------- Event log ----------
    def add_event(self, *, kind: str, data: Dict[str, Any]) -> None:
        """
        Append an event to the timeline and persist.
        Events are strictly chronological and are the single source of truth for the playbook.
        """
        evt = {
            "ts": time.time(),
            "kind": kind,           # 'decision' | 'param_binding' | 'tool_started' | 'tool_finished' | 'slot_mapped' | 'exit' | 'error'
            **(data or {}),
        }
        self.events.append(evt)

        # index tool_started → artifact_name
        if kind == "tool_started":
            art_name = (data.get("artifact_name") or "").strip()
            if art_name:
                self.tool_call_index[art_name] = {
                    "signature": data.get("signature"),
                    "tool_id": data.get("tool_id"),
                    "started_ts": evt["ts"],
                }

        self.persist()

    # ---------- Tool results ----------
    def register_tool_result(
            self,
            *,
            artifact_id: str,
            tool_id: str,
            value: Any,
            summary: str,
            sources_used: List[Dict[str, Any]] | None = None,
            inputs: Dict[str, Any] | None = None,
            call_record_rel: str | None = None,
            call_record_abs: str | None = None,
            artifact_type: Optional[str] = None,
            error: Optional[Dict[str, Any]] = None,
            content_inventorization: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Store a current-turn tool result as an artifact object.
        For write_* tools, normalize .value to a *file artifact* dict:
          {"type":"file","path":<str>,"text":<surrogate>,"mime":<mime>[,"filename":<str>,"sources_used":[...]]}
        """
        value_norm = value

        if tools_insights.is_write_tool(tool_id):
            # Extract file path from return
            if isinstance(value, dict) and isinstance(value.get("path"), str) and value["path"].strip():
                file_path = value["path"].strip()
            elif isinstance(value, str) and value.strip():
                file_path = value.strip()
            else:
                file_path = ""

            # Build surrogate + mime from the tool inputs (BYTES require description+mime)
            surrogate_text, mime_hint = self._surrogate_from_writer_inputs(tool_id=tool_id, inputs=(inputs or {}))
            value_norm = {
                "type": "file",
                "path": file_path,
                "text": (surrogate_text or ""),
                "mime": (mime_hint or tools_insights.default_mime_for_write_tool(tool_id)),
            }

            # propagate sources into value for uniform consumption
            if sources_used:
                try:
                    value_norm["sources_used"] = list(sources_used)
                except Exception:
                    pass

            # convenience: filename derived from path
            try:
                if file_path:
                    from pathlib import Path
                    value_norm["filename"] = Path(file_path).name
            except Exception:
                pass

            # optional guard for write_file with BYTES missing description
            if tool_id == "generic_tools.write_file":
                content = (inputs or {}).get("content")
                if isinstance(content, (bytes, bytearray)) and not (inputs or {}).get("content_description"):
                    self.add_event(kind="error", data={
                        "reason": "writer_inputs_missing_content_description",
                        "tool_id": tool_id,
                        "artifact_id": artifact_id,
                    })

        artifact = {
            "tool_id": tool_id,
            "value": value_norm,
            "summary": str(summary or ""),
            "sources_used": list(sources_used or []),
            "timestamp": time.time(),
            "inputs": dict(inputs or {}),
            "call_record": {
                "rel": call_record_rel,
                "abs": call_record_abs,
            },
            "artifact_type": artifact_type,
        }
        # Add error if present
        if error:
            artifact["error"] = error
        if content_inventorization is not None:
            artifact["content_inventorization"] = content_inventorization
        self.current_tool_results[artifact_id] = artifact
        self.persist()
        return artifact


    # ---------- Slot mapping ----------
    def map_inline_slot(
            self,
            *,
            slot_name: str,
            slot_spec: SlotSpec,
            source_value: Any,
            sources_used: List[Dict[str, Any]] | None = None,
            tool_id: Optional[str] = None,
            summary: Optional[str] = None,
            draft: bool = False,
            gaps: Optional[str] = None,
            content_inventorization: Optional[Any] = None,
    ) -> Dict[str, Any]:
        fmt = slot_spec.format or "text"  # Pydantic attribute access
        text_repr = _text_repr(source_value)

        gaps_clean = (gaps or "").strip() if gaps else ""

        artifact: Dict[str, Any] = {
            "type": "inline",
            "format": fmt,
            "text": text_repr,
            "value": source_value,
            "sources_used": list(sources_used or []),
            "tool_id": tool_id,
            "description": slot_spec.description or "",
            "summary": summary,
        }
        if draft:
            artifact["draft"] = True
        if gaps_clean:
            artifact["gaps"] = gaps_clean
        if content_inventorization is not None:
            artifact["content_inventorization"] = content_inventorization


        self.current_slots[slot_name] = artifact
        self.persist()
        self.add_event(
            kind="slot_mapped",
            data={
                "slot_name": slot_name,
                "slot_type": "inline",
                "len": len(text_repr),
                "draft": bool(draft),
                "has_gaps": bool(gaps_clean),
            },
        )
        return artifact


    def map_file_slot(
            self,
            *,
            slot_name: str,
            slot_spec: SlotSpec,
            surrogate_text: str,
            file_path: str,
            sources_used: List[Dict[str, Any]] | None = None,
            tool_id: Optional[str] = None,
            summary: Optional[str] = None,
            mime_override: Optional[str] = None,
            draft: bool = False,
            gaps: Optional[str] = None,
            content_inventorization: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        FILE SLOT GATE: caller is responsible for ensuring BOTH surrogate and file_path are ready.
        """
        gaps_clean = (gaps or "").strip() if gaps else ""

        artifact: Dict[str, Any] = {
            "type": "file",
            "mime": (mime_override or slot_spec.mime or "application/octet-stream"),
            "path": file_path,
            "text": surrogate_text,  # authoritative text surrogate
            "sources_used": list(sources_used or []),
            "tool_id": tool_id,
            "description": slot_spec.description or "",
            "summary": summary,
        }
        if draft:
            artifact["draft"] = True
        if gaps_clean:
            artifact["gaps"] = gaps_clean
        if content_inventorization is not None:
            artifact["content_inventorization"] = content_inventorization

        self.current_slots[slot_name] = artifact
        self.persist()
        self.add_event(
            kind="slot_mapped",
            data={
                "slot_name": slot_name,
                "slot_type": "file",
                "len": len(surrogate_text),
                "path": file_path,
                "draft": bool(draft),
                "has_gaps": bool(gaps_clean),
            },
        )
        return artifact


    # ---------- Object resolution ----------
    def resolve_object(self, path: str) -> Optional[Dict[str, Any]]:
        """
        Resolve an OBJECT path (not a leaf). Returns the artifact dict or None.

        Supported:
          - current_turn.tool_results.<artifact_id>
          - current_turn.slots.<slot_name>
          - <turn_id>.slots.<slot_name>
        """
        if not path or not isinstance(path, str):
            return None

        parts = path.split(".")
        # current turn: tool_results.<id>
        if len(parts) == 3 and parts[0] == "current_turn" and parts[1] == "tool_results":
            return self.current_tool_results.get(parts[2])

        # current turn: slots.<slot>
        if len(parts) == 3 and parts[0] == "current_turn" and parts[1] == "slots":
            return self.current_slots.get(parts[2])

        # prior turn: <turn_id>.slots.<slot>
        if len(parts) == 3 and parts[1] == "slots":
            turn = self.prior_turns.get(parts[0]) or {}
            slots = (turn.get("deliverables") or {})
            pack = slots.get(parts[2]) or {}
            # deliverable may be stored as {"value": {...}, "description": "..."} or just the artifact dict
            if isinstance(pack, dict) and "value" in pack and isinstance(pack.get("value"), dict):
                return pack["value"]
            return pack if isinstance(pack, dict) else None

        return None

    def _resolve_rendered_file_path(self, produced: dict | None) -> tuple[str | None, str]:
        """
        Return (file_path, source_artifact_id_for_log).
        Accepts:
          - write_* result with .value as string path
          - write_* result with .value.path dict form
        Fallback: latest unique write_* with a usable path in current turn.
        """

        # 1) Explicit produced
        if isinstance(produced, dict):
            tid = produced.get("tool_id") or ""
            v = produced.get("value")
            if tools_insights.is_write_tool(tid):
                if isinstance(v, str) and v.strip():
                    return v, "(produced)"
                if isinstance(v, dict) and isinstance(v.get("path"), str) and v["path"].strip():
                    return v["path"], "(produced)"

        # 2) Fallback: scan latest 5 results for a single unambiguous write_* path
        candidates = []
        items = sorted(
            ((k, v) for k, v in self.current_tool_results.items() if isinstance(v, dict)),
            key=lambda kv: float(kv[1].get("timestamp") or 0.0),
            reverse=True
        )[:5]
        for art_id, art in items:
            tid = art.get("tool_id") or ""
            if not tools_insights.is_write_tool(tid):
                continue
            v = art.get("value")
            if isinstance(v, str) and v.strip():
                candidates.append((art_id, v))
            elif isinstance(v, dict) and isinstance(v.get("path"), str) and v["path"].strip():
                candidates.append((art_id, v["path"]))

        if len(candidates) == 1:
            return candidates[0][1], candidates[0][0]
        return None, "(ambiguous)" if candidates else "(none)"

    # ---------- Slot mapping from decision ----------
    def map_from_decision(
            self,
            *,
            decision: Dict[str, Any],
            output_contract: Dict[str, Any],
            logger: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Apply decision.map_slot.

        INLINE:
          - Prefer mapping from a LEAF textual path (e.g., ...tool_results.<id>.value.content).
          - Fallback: map from an OBJECT (extracting value.content/value.text).

        FILE:
          - Primary: compatible *file artifact*:
              value.type == "file" and string value.text and string value.path
            Valid sources:
              • Existing file slot (current/prior)
              • Writer result this turn (under .value)
          - Fallback (draft only): degrade to a synthetic file backed by an inline text surrogate.
        """
        ms = (decision or {}).get("map_slot") or {}
        slot_name = (ms.get("slot_name") or "").strip()
        art_path  = (ms.get("artifact") or ms.get("source_path") or "").strip()  # tolerate old field name

        if not slot_name:
            return None

        # Draft + gaps flags from MapSlotDirective
        draft_flag = bool(ms.get("draft"))
        gaps = ms.get("gaps")
        if isinstance(gaps, str):
            gaps = gaps.strip()
            # if len(gaps_text) > 200:
            #     gaps_text = gaps_text[:200]
        else:
            gaps = ""

        def _log(kind: str, payload: Dict[str, Any]):
            # enrich payload a bit with draft/gaps info where useful
            base = dict(payload)
            if draft_flag:
                base.setdefault("draft", True)
            if gaps:
                base.setdefault("gaps", gaps)
            self.add_event(kind=kind, data=base)
            if logger:
                try:
                    logger.log(f"[react.map] {kind}: {json.dumps(base, ensure_ascii=False)[:400]}")
                except Exception:
                    pass

        # --- Slot spec lookup ---
        slot_spec = (output_contract or {}).get(slot_name)
        if not slot_spec or not isinstance(slot_spec, SlotSpec):
            _log("mapping_skipped", {"slot": slot_name, "reason": "slot_spec_missing", "artifact": art_path})
            return None
        slot_type = (slot_spec.type or "inline").lower()
        summary = None
        content_inventorization = None
        # ---------- INLINE ----------
        if slot_type == "inline":

            # 1) Leaf-first (preferred)
            val, owner = self.resolve_path(art_path, mode="full") if art_path else (None, None)
            if isinstance(val, str) and val.strip():
                sources_used = (owner or {}).get("sources_used") or []
                tool_id = (owner or {}).get("tool_id")

                # If leaf is under current_turn.tool_results.*, also look into .value.sources_used
                if art_path.startswith("current_turn.tool_results."):
                    rel = art_path[len("current_turn.tool_results."):]
                    art_id = rel.split(".", 1)[0]
                    tr_obj = self.current_tool_results.get(art_id) or {}
                    if not sources_used:
                        v_su = ((tr_obj.get("value") or {}).get("sources_used") or [])
                        if isinstance(v_su, list) and v_su:
                            sources_used = v_su
                    if not tool_id:
                        tool_id = tr_obj.get("tool_id")
                    inv = tr_obj.get("content_inventorization")
                    if inv is not None:
                        content_inventorization = inv
                    summary = tr_obj.get("summary")

                art = self.map_inline_slot(
                    slot_name=slot_name,
                    slot_spec=slot_spec,
                    source_value=val,
                    sources_used=sources_used,
                    tool_id=tool_id,
                    draft=draft_flag,
                    gaps=gaps or None,
                    content_inventorization=content_inventorization,
                    summary=summary
                )
                _log("slot_mapped_ok", {"slot": slot_name, "type": "inline", "len": len(val), "from": "leaf"})
                return art

            # 2) Fallback: OBJECT path (legacy behavior)
            src_obj = self.resolve_object(art_path) if art_path else None
            if isinstance(src_obj, dict):
                text, sources_used = _extract_text_and_sources(src_obj)
                if isinstance(text, str) and text.strip():
                    art = self.map_inline_slot(
                        slot_name=slot_name,
                        slot_spec=slot_spec,
                        source_value=text,
                        sources_used=sources_used,
                        tool_id=src_obj.get("tool_id"),
                        draft=draft_flag,
                        gaps=gaps or None,
                        content_inventorization=src_obj.get("content_inventorization"),
                        summary=src_obj.get("summary"),
                    )
                    _log("slot_mapped_ok", {"slot": slot_name, "type": "inline", "len": len(text), "from": "object"})
                    return art

            _log("mapping_skipped", {"slot": slot_name, "reason": "inline_text_missing_or_bad_path", "artifact": art_path})
            return None

        # ---------- FILE ----------
        def _as_file_artifact(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            # direct file artifact (slot)
            if (obj.get("type") == "file" and isinstance(obj.get("path"), str) and isinstance(obj.get("text"), str)):
                return obj
            # writer result normalized under .value
            v = obj.get("value")
            if isinstance(v, dict) and v.get("type") == "file" and isinstance(v.get("path"), str) and isinstance(v.get("text"), str):
                return v
            return None

        # ---------- FILE BRANCH ----------
        # Slot spec info
        if hasattr(slot_spec, "model_dump"):
            spec_dict = slot_spec.model_dump()
        else:
            spec_dict = dict(slot_spec) if isinstance(slot_spec, dict) else {}

        desc = (spec_dict.get("description") or "").strip()
        mime = (spec_dict.get("mime") or "application/octet-stream").strip() or "application/octet-stream"

        src_obj = self.resolve_object(art_path) if art_path else None

        # Try to see if it's already a file artifact
        file_art = _as_file_artifact(src_obj) if isinstance(src_obj, dict) else None

        # 1) Primary path: actual file artifact
        if file_art:
            art = dict(file_art)
            art["resource_id"] = f"slot:{slot_name}"
            art.setdefault("type", "file")
            art.setdefault("mime", mime)
            if desc and not art.get("description"):
                art["description"] = desc
            if draft_flag:
                art["draft"] = True
            if gaps:
                art["gaps"] = gaps

            self.current_slots[slot_name] = art
            _log("mapping_applied_file", {
                "slot": slot_name,
                "artifact": art_path,
                "draft": draft_flag,
                "gaps": gaps,
            })
            return art

        # 2) Fallback path: draft file slot backed by inline text surrogate
        if draft_flag and art_path:
            text = None
            sources_used: list[dict] | None = None
            owner_tool_id = None
            actual_owner = src_obj

            # Prefer extracting from the object if we have one
            if isinstance(src_obj, dict):
                text, sources_used = _extract_text_and_sources(src_obj)
                owner_tool_id = src_obj.get("tool_id")

            # If that didn't work, fall back to leaf resolution
            if not text:
                val, owner = self.resolve_path(art_path, mode="full")
                if isinstance(val, str) and val.strip():
                    text = val
                    owner_tool_id = (owner or {}).get("tool_id")
                    actual_owner = owner

            if isinstance(text, str) and text.strip():
                art = {
                    "resource_id": f"slot:{slot_name}",
                    "type": "file",
                    "tool_id": owner_tool_id or "program",
                    "mime": mime,
                    "description": desc,
                    "path": "",   # synthetic file – no real path
                    "text": text,
                    "draft": True,
                }
                if gaps:
                    art["gaps"] = gaps
                if sources_used:
                    art["sources_used"] = sources_used
                if isinstance(actual_owner, dict) and actual_owner.get("content_inventorization") is not None:
                    art["content_inventorization"] = actual_owner.get("content_inventorization")
                if isinstance(actual_owner, dict) and actual_owner.get("summary") is not None:
                    art["summary"] = actual_owner.get("summary")
                self.current_slots[slot_name] = art
                _log("mapping_degraded_file_from_inline", {
                    "slot": slot_name,
                    "artifact": art_path,
                    "draft": True,
                    "gaps": gaps,
                    "mime": mime,
                })
                return art

        # 3) Nothing usable → log and bail
        reason = "artifact_not_compatible_file" if isinstance(src_obj, dict) else "artifact_not_found_or_not_object"
        _log("mapping_skipped", {
            "slot": slot_name,
            "reason": reason,
            "artifact": art_path,
        })
        return None


    # ---------- Path resolution ----------
    def resolve_path(self, path: str, *, mode: str = "summary") -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        """
        Resolve a dot-path to a primitive leaf value.
        Returns (value, owning_object_or_None).

        Valid leaves (general): .text, .value, .summary, .path, .format, .mime, .filename, <message-string>

        SPECIAL CASE (tool_results with structured values):
          - You may traverse INSIDE .value via dotted keys, e.g.:
              current_turn.tool_results.<id>.value.content
              current_turn.tool_results.<id>.value.format
              current_turn.tool_results.<id>.value.stats.rounds
            If .value is a JSON STRING, it will be auto-parsed for traversal.
            The final resolved item must be primitive (string/number/bool) or bytes;
            otherwise it will be serialized upstream when binding (as today).
        """
        if not path or not isinstance(path, str):
            return None, None

        # ---------- Messages (past turns) ----------
        if path.endswith(".user") or path.endswith(".assistant"):
            pieces = path.split(".")
            if len(pieces) == 2:
                turn_id, leaf = pieces
                turn = self.prior_turns.get(turn_id) or {}

                # '.user' means we want the user **prompt** string
                if leaf == "user":
                    val = ((turn.get("user") or {}).get("prompt") or "")
                elif leaf == "assistant":
                    val = (turn.get("assistant") or "")
                else:
                    # fallback (shouldn't happen with allowed leaves)
                    val = (turn.get(leaf) or "")

                # For messages, summary mode returns ≤ ~600 chars
                if mode == "summary" and isinstance(val, str) and len(val) > _SUMMARY_MAX:
                    val = val[:_SUMMARY_MAX] + "…"
                return val, {"_kind": "message", "turn_id": turn_id}

        # ---------- Current turn tool results ----------
        if path.startswith("current_turn.tool_results."):
            rel = path.replace("current_turn.tool_results.", "")

            # 1) Simple known leaves first (value/summary/text/path/format/mime/filename)
            maybe = _dig(self.current_tool_results, rel)
            if isinstance(maybe, tuple):
                val, parent = maybe
                leaf = rel.split(".")[-1]
                # Only .text leaves can be truncated; for others keep full value.
                return _summarize_if_needed(val, mode, leaf=leaf), parent

            # 2) Structured traversal under ".value.*"
            #    Support: current_turn.tool_results.<id>.value.<nested.dotted.path>
            parts = rel.split(".", 2)  # at most three: <id>, "value", "<rest>"
            if len(parts) >= 2 and parts[1] == "value":
                art = self.current_tool_results.get(parts[0])
                if isinstance(art, dict):
                    parent = art
                    v = art.get("value")

                    # If the *entire* .value is a JSON string, parse it once up front
                    if isinstance(v, str):
                        try:
                            v = json.loads(v)
                        except Exception:
                            # leave as-is; traversal may still fail, which is fine
                            pass

                    if len(parts) == 3:
                        cur = v
                        rest = parts[2]
                        segments = rest.split(".")
                        ok = True

                        # Traverse dot segments; support dicts and lists (list indices as numeric segments).
                        # IMPORTANT: after *each* step, if cur is a string and we still have segments
                        # to walk, we interpret it as JSON and continue.
                        for idx, seg in enumerate(segments):
                            # Step into the next segment
                            if isinstance(cur, list):
                                try:
                                    i = int(seg)
                                except Exception:
                                    ok = False
                                    break
                                if 0 <= i < len(cur):
                                    cur = cur[i]
                                else:
                                    ok = False
                                    break
                            elif isinstance(cur, dict):
                                if seg in cur:
                                    cur = cur[seg]
                                else:
                                    ok = False
                                    break
                            else:
                                # Current node is neither list nor dict: if this is NOT the last
                                # segment, we cannot go further.
                                ok = False
                                break

                            # If there are still segments left and cur is a JSON string,
                            # parse it so we can keep traversing.
                            if isinstance(cur, str) and idx < len(segments) - 1:
                                try:
                                    cur = json.loads(cur)
                                except Exception:
                                    ok = False
                                    break

                        if ok:
                            val = cur
                            # For structured values, only truncate long string leaves in summary mode
                            if mode == "summary" and isinstance(val, str) and len(val) > _SUMMARY_MAX:
                                val = val[:_SUMMARY_MAX] + "…"
                            return val, parent
                        else:
                            # Helpful debug log for future diagnostics; does not change behavior.
                            try:
                                cur_type = type(cur).__name__
                            except Exception:
                                cur_type = "unknown"
                            log.debug(
                                "[resolve_path] structured .value traversal failed",
                                extra={
                                    "path": path,
                                    "rel": rel,
                                    "segments": segments,
                                    "current_type": cur_type,
                                },
                            )

            # 3) Fallback: try resolving as a normal leaf within current_turn.tool_results
            leaf_val, parent = _resolve_leaf_path(self.current_tool_results, rel, mode)
            return leaf_val, parent

        # ---------- Current turn slots ----------
        # (strict: only standard leaves; no nested .value.* traversal here)
        if path.startswith("current_turn.slots."):
            rel = path.replace("current_turn.slots.", "")
            leaf_val, parent = _resolve_slot_with_value_fallback(self.current_slots, rel, mode)
            return leaf_val, parent

        # ---------- Past turn slots ----------
        if ".slots." in path:
            turn_id, _, rest = path.partition(".slots.")
            turn = self.prior_turns.get(turn_id) or {}
            slots = (turn.get("deliverables") or {})
            leaf_val, parent = _resolve_slot_with_value_fallback(slots, rest, mode)
            return leaf_val, parent

        # ---------- Fallback: generic leaf resolution over current turn namespaces ----------
        leaf_val, parent = _resolve_leaf_path(
            {"tool_results": self.current_tool_results, "slots": self.current_slots},
            path.replace("current_turn.", ""),
            mode,
        )
        return leaf_val, parent

    # ---------- Param binding ----------
    def bind_params(
            self,
            *,
            base_params: Dict[str, Any],
            fetch_directives: List[Dict[str, Any]],
            tool_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Apply fetch directives (leaf-only) to tool params.

        Default behavior:
          - For most params: resolve leaves via resolve_path and concatenate
            multiple contributions with two newlines.

        Special behavior for tools that declare sources params:
          - If tools_insights.wants_sources_param(tool_id) or
            tools_insights.wants_sources_json(tool_id) is true, then params
            named "sources" / "sources_json" are treated as JSON lists:
              * Inline value (base_params[...] or params[...]) is parsed as JSON
                (list or dict).
              * Each fetched contribution is also parsed as JSON if string,
                or used directly if it's list/dict.
              * All rows are flattened into one list, normalized & deduped via
                _reconcile_sources_lists (materializing into pool + SIDs).
              * The final param is stored as a single JSON string.
        """
        params: Dict[str, Any] = dict(base_params or {})

        # Which special behavior do we need?
        wants_sources_json = tools_insights.wants_sources_json(tool_id=tool_id)
        wants_sources_param = tools_insights.wants_sources_param(tool_id=tool_id)

        # Buckets:
        # - normal_buckets → string concatenation
        # - sources_buckets → raw contributions for "sources"/"sources_json"
        normal_buckets: Dict[str, List[str]] = {}
        sources_buckets: Dict[str, List[Any]] = {}

        for fd in (fetch_directives or []):
            p = (fd or {}).get("path")
            name = (fd or {}).get("param_name")
            mode = (fd or {}).get("mode") or "summary"
            if not (p and name):
                continue
            if (wants_sources_json or wants_sources_param) and name in {"sources", "sources_json"} and mode == "summary":
                log.warning(
                    f"[bind_params] Overriding fetch mode 'summary' → 'full' "
                    f"for sources-like param '{name}' of tool '{tool_id}'"
                )
                mode = "full"  # for sources we want full data

            val, parent = self.resolve_path(p, mode=mode)
            if val is None:
                continue

            # Avoid piping fetch_uri_content artifacts into generative builtin tool's input_context param,
            # but still keep this fetch directive so _collect_sources_from_fetch()
            # can harvest their sources_used.
            if tools_insights.is_generative_tool(tool_id) and name == "input_context" and isinstance(parent, dict):
                src_tool_id = parent.get("tool_id")
                try:
                    if tools_insights.is_fetch_uri_content_tool(src_tool_id):
                        log.debug(
                            "[bind_params] Skipping fetch_uri_content artifact "
                            f"for input_context (path={p}, src_tool={src_tool_id}) "
                            "to avoid duplicating content already passed via sources_json."
                        )
                        # IMPORTANT: we only skip *text* binding; the directive itself
                        # stays in fetch_directives and will still be visible to
                        # _collect_sources_from_fetch() used by bind_params_with_sources().
                        continue
                except Exception:
                    # Fail-safe: if anything goes wrong, fall back to old behavior
                    pass

            # Special handling only for tools that actually accept sources params
            if name in {"sources", "sources_json"} and (
                (name == "sources_json" and wants_sources_json)
                or (name == "sources" and wants_sources_param)
            ):
                # For sources params we keep raw values (list/dict/str) and
                # will parse/normalize them later.
                sources_buckets.setdefault(name, []).append(val)
                continue

            # Regular param: convert to string and bucket for concatenation
            if not isinstance(val, (str, bytes)):
                try:
                    val = json.dumps(val, ensure_ascii=False)
                except Exception:
                    val = str(val)
            s = val if isinstance(val, str) else val.decode("utf-8")
            normal_buckets.setdefault(name, []).append(s)

        # 1) Regular params: old behavior (string concatenation with \n\n)
        for k, parts in normal_buckets.items():
            joined = "\n\n".join([s.strip() for s in parts if s])
            existing = params.get(k)
            if isinstance(existing, str) and existing.strip():
                params[k] = (existing.strip() + "\n\n" + joined).strip()
            else:
                params[k] = joined

        # 2) Special merging for sources / sources_json
        def _gather_sources_for_param(param_name: str) -> List[Dict[str, Any]]:
            """
            Collect inline + fetched contribution for a single sources-like param,
            normalize them into a flat list[dict].
            """
            rows: List[Any] = []

            # Inline/base contribution
            inline = params.get(param_name)
            if isinstance(inline, list):
                rows.extend(inline)
            elif isinstance(inline, dict):
                rows.append(inline)
            elif isinstance(inline, str) and inline.strip():
                # Inline string is expected to be JSON list or dict
                try:
                    parsed = json.loads(inline)
                    if isinstance(parsed, list):
                        rows.extend(parsed)
                    elif isinstance(parsed, dict):
                        rows.append(parsed)
                except Exception:
                    # If inline is garbage, we ignore it for safety
                    pass

            # Fetched contributions
            for v in sources_buckets.get(param_name, []):
                if isinstance(v, list):
                    rows.extend(v)
                elif isinstance(v, dict):
                    rows.append(v)
                elif isinstance(v, str):
                    try:
                        parsed = json.loads(v)
                        if isinstance(parsed, list):
                            rows.extend(parsed)
                        elif isinstance(parsed, dict):
                            rows.append(parsed)
                    except Exception:
                        # Ignore non-JSON strings for sources
                        pass

            # Filter to dicts only — _reconcile_sources_lists will do further normalization
            return [r for r in rows if isinstance(r, dict)]

        # Only apply special logic for tools that declare they want these params
        # AND when there is something to merge.
        if wants_sources_json:
            flat = _gather_sources_for_param("sources_json")
            if flat:
                merged = self._reconcile_sources_lists([flat])
                params["sources_json"] = json.dumps(merged, ensure_ascii=False)

        if wants_sources_param:
            flat = _gather_sources_for_param("sources")
            if flat:
                merged = self._reconcile_sources_lists([flat])
                params["sources"] = json.dumps(merged, ensure_ascii=False)

        # Log event
        bound_keys = list(normal_buckets.keys()) + list(sources_buckets.keys())
        if bound_keys:
            self.add_event(kind="param_binding", data={"params_bound": bound_keys})

        return params

    # ---------- Sources utilities ----------
    def _reconcile_sources_lists(self, lists: list[list[dict] | None]) -> list[dict]:
        """
        Normalize + dedupe by URL; assign fresh SIDs for rows missing 'sid',
        monotonically increasing from self.max_sid. Persist updated max_sid.
        """
        combined: list[dict] = []
        for li in (lists or []):
            combined.extend(normalize_sources_any(li))
        if not combined:
            return []

        deduped = dedupe_sources_by_url([], combined)
        # Assign SIDs for items that don't have one
        for row in deduped:
            sid = row.get("sid")
            if not isinstance(sid, int) or sid <= 0:
                self.max_sid += 1
                row["sid"] = int(self.max_sid)

        # Persist updated counter
        self.persist()
        return deduped

    def _collect_sources_from_fetch(self, fetch_directives: list[dict]) -> list[dict]:
        """
        For each fetch directive, look at the owning object of the resolved path.
        If it carries 'sources_used', collect them.
        Fallback: if the resolved value looks like {url -> {...}}, treat each entry as a source.
        """
        acc: list[dict] = []

        for fd in (fetch_directives or []):
            if not isinstance(fd, dict):
                continue
            p = (fd.get("path") or "").strip()
            if not p:
                continue

            # For sources we always want full, non-truncated values
            val, parent = self.resolve_path(p, mode="full")

            candidates: list[dict] = []

            # 1) Preferred: explicit sources_used on the parent artifact
            if isinstance(parent, dict):
                su = parent.get("sources_used")
                if su:
                    candidates.extend(normalize_sources_any(su))

            if candidates:
                acc.extend(normalize_sources_any(candidates))

        return acc

    def _parse_sources_param_value(self, raw: Any) -> list[dict]:
        """
        Parse 'sources*' params (string or list) into a flat list[dict].

        Accepts:
          - JSON string for a list or dict
          - multiple JSON chunks concatenated with two newlines
          - already a list of dicts

        Drops items that do not have a string 'url'.
        """
        rows: list[Any] = []
        if isinstance(raw, list):
            rows = list(raw)
        elif isinstance(raw, str):
            # bind_params concatenates with two newlines, so split on that,
            # but also handle the single-chunk case.
            segments = [seg for seg in raw.split("\n\n") if seg.strip()] or [raw]
            for seg in segments:
                try:
                    parsed = json.loads(seg)
                except Exception:
                    continue
                if isinstance(parsed, list):
                    rows.extend(parsed)
                elif isinstance(parsed, dict):
                    rows.append(parsed)
        else:
            return []

        clean: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            url = r.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            # keep title only if it's a string
            if "title" in r and not isinstance(r["title"], str):
                r = dict(r)
                r.pop("title", None)
            clean.append(r)
        return clean

    def bind_params_with_sources(
            self,
            *,
            base_params: dict[str, Any],
            fetch_directives: list[dict],
            tool_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        1) Do the regular text binding (concatenate leaves into params).
        2) For citations-aware tools or when sources/sources_json is explicitly present:
           - Gather sources from fetched artifacts' owners
           - Merge with any caller-provided sources
           - Insert as correct param ('sources_json' for LLM generator, 'sources' for write tools), stringified
        """
        params = self.bind_params(base_params=base_params,
                                  fetch_directives=fetch_directives,
                                  tool_id=tool_id)

        # tools we auto-attach sources to (even if caller didn't map 'sources' explicitly)
        is_citation_aware = tools_insights.does_tool_accept_sources(tool_id)

        # choose which param this tool expects
        wants_sources_json = tools_insights.wants_sources_json(tool_id=tool_id)
        wants_sources_param = tools_insights.wants_sources_param(tool_id=tool_id)

        # detect explicit mapping
        explicitly_requests_sources = any(
            (fd or {}).get("param_name") in {"sources", "sources_json"} for fd in (fetch_directives or [])
        ) or ("sources" in (base_params or {})) or ("sources_json" in (base_params or {}))

        if not (is_citation_aware or explicitly_requests_sources):
            return params

        # gather sources from referenced artifacts (inputs)
        from_fetch = self._collect_sources_from_fetch(fetch_directives)

        # gather any provided sources
        provided_list: List[Dict[str, Any]] = []
        if "sources_json" in params:
            provided_list = self._parse_sources_param_value(params["sources_json"])
        elif "sources" in params:
            provided_list = self._parse_sources_param_value(params["sources"])

        # merge + dedupe in-engine (local, not touching pool)
        merged = self._reconcile_sources_lists([from_fetch, provided_list])

        if merged:
            # For LLM + writer tools, normalize into a clean shape
            if wants_sources_json or wants_sources_param:
                merged = [adapt_source_for_llm(s) for s in merged if isinstance(s, dict)]

            payload = json.dumps(merged, ensure_ascii=False)
            if wants_sources_json:
                params["sources_json"] = payload
            elif wants_sources_param:
                params["sources"] = payload
            else:
                # fallback: prefer 'sources' if tool unknown but asked explicitly
                target_key = "sources_json" if ("sources_json" in (base_params or {})) else "sources"
                params[target_key] = payload

            self.add_event(kind="param_binding_sources", data={
                "tool": tool_id,
                "merged_sources": len(merged),
                "from_fetch": len(normalize_sources_any(from_fetch)),
                "from_params": len(normalize_sources_any(provided_list)),
            })

        return params

# ---------- Helpers ----------

def _dig(root: Dict[str, Any], path: str) -> Optional[Tuple[Any, Dict[str, Any]]]:
    """
    Resolve 'artifact_id.leaf' within a dict and return (leaf_value, artifact_object).
    Only returns tuple for known primitive leaves; otherwise None.
    """
    if not root or not path:
        return None
    parts = path.split(".")
    if len(parts) < 2:
        return None
    art_id, leaf = parts[0], ".".join(parts[1:])
    obj = root.get(art_id)
    if not isinstance(obj, dict):
        return None
    if leaf in {"value", "summary", "text", "path", "format", "mime", "filename"}:
        return obj.get(leaf), obj
    return None

def _resolve_leaf_path(namespace: Dict[str, Any], rel_path: str, mode: str) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """
    Resolve rel_path like '<key>.<leaf>' inside provided namespace (dict of dicts).
    Returns (value, owning_obj).
    """
    if not namespace or not rel_path:
        return None, None

    # First, try exact leaf hit
    maybe = _dig(namespace, rel_path)
    if maybe:
        val, parent = maybe
        return _summarize_if_needed(val, mode, leaf=rel_path.split(".")[-1]), parent

    # Next, permit root access ONLY when it’s a message-like string (safety)
    # For slots and tool_results, root is an object → NOT allowed as a leaf.
    parts = rel_path.split(".")
    if len(parts) == 1:
        obj = namespace.get(parts[0])
        if isinstance(obj, str):
            return _summarize_if_needed(obj, mode, leaf="value"), {"_kind": "string"}
        return None, None

    # Finally, walk dotted leaves safely
    head, *tail = parts
    obj = namespace.get(head)
    if not isinstance(obj, dict):
        return None, None

    leaf = tail[-1]
    if leaf not in {"value", "summary", "text", "path", "format", "mime", "filename"}:
        return None, None

    # Accept only primitive leaf values
    cur = obj
    for key in tail:
        cur = cur.get(key) if isinstance(cur, dict) else None
        if cur is None:
            return None, obj
    return _summarize_if_needed(cur, mode, leaf=leaf), obj

def _resolve_slot_with_value_fallback(slots: Dict[str, Any], rel_path: str, mode: str):
    # 1) Try as-is
    val, parent = _resolve_leaf_path(slots, rel_path, mode)
    if val is not None:
        return val, parent

    # 2) Try wrapper '.value.'
    parts = rel_path.split(".", 1)
    if not parts:
        return None, None
    slot_key = parts[0]
    rest = parts[1] if len(parts) == 2 else ""
    alt = slot_key + ".value" + (("." + rest) if rest else "")
    val2, parent2 = _resolve_leaf_path(slots, alt, mode)
    if val2 is not None:
        # Prefer inner artifact dict as parent
        if isinstance(parent2, dict) and isinstance(parent2.get("value"), dict):
            return val2, parent2["value"]
        return val2, parent2

    # 3) NEW: alias '.text' → '.content' for INLINE slots only
    # Only do this when the *requested* leaf is 'text'
    requested_leaf = (rest.split(".")[-1] if rest else "").strip() if len(parts) == 2 else ""
    if requested_leaf == "text":
        slot_obj = slots.get(slot_key)
        # unwrap if needed
        art = slot_obj.get("value") if (isinstance(slot_obj, dict) and isinstance(slot_obj.get("value"), dict)) else slot_obj
        if isinstance(art, dict):
            # Inline slot often has: {"type":"inline", "content": "..."} in some histories
            if (art.get("type") == "inline") and isinstance(art.get("content"), str):
                v = art.get("content")
                if mode == "summary" and isinstance(v, str) and len(v) > _SUMMARY_MAX:
                    v = v[:_SUMMARY_MAX] + "…"
                return v, art

    return None, None


def _summarize_if_needed(value: Any, mode: str, *, leaf: str) -> Any:
    if mode != "summary":
        return value
    # Only .text leaves can be truncated for slots; messages/tool-results summaries are precomputed
    if leaf == "text" and isinstance(value, str) and len(value) > _SUMMARY_MAX:
        return value[:_SUMMARY_MAX] + "…"
    return value

def _text_repr(val: Any) -> str:
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False, indent=2)
    except Exception:
        return str(val)


