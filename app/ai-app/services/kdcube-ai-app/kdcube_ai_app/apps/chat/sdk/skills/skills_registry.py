# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/skills/skills_registry.py

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Iterable, Tuple, Set, Literal
import fnmatch
import pathlib
import logging
import textwrap

import yaml  # PyYAML; if you don't want this dependency, we can switch to JSON.

log = logging.getLogger("skills_registry")

BUILTIN_SKILLS_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent
try:
    from kdcube_ai_app.apps.chat.sdk.tools.backends.web.ranking import estimate_tokens  # type: ignore
except Exception:
    estimate_tokens = None

SkillConsumer = str


SKILLS_DESCRIPTOR_CV: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "SKILLS_DESCRIPTOR_CV", default=None
)
SKILLS_SUBSYSTEM_CV: ContextVar[Optional["SkillsSubsystem"]] = ContextVar(
    "SKILLS_SUBSYSTEM_CV", default=None
)


@dataclass
class SkillInstructionPaths:
    """Paths to instruction snippets, relative to bundle root."""
    full: Optional[pathlib.Path] = None
    compact: Optional[pathlib.Path] = None


@dataclass
class SkillToolRef:
    """Reference to a tool that belongs to this skill."""
    id: str
    role: Optional[str] = None  # e.g. "search", "fetch", "writer", "reconcile"
    why: Optional[str] = None


@dataclass
class SkillSpec:
    """
    Declarative description of a 'skill' – a bundle of related tools + instructions.

    - id:         short identifier used in prompts and selection decisions
    - name:       human-readable label shown in the 'skills gallery'
    - description: short description of what the skill gives
    - tools:      list of tool ids (alias.fn) associated with this skill
    - instruction_paths: paths to full / compact instruction snippets
    - built_in:   if true, include in default galleries for relevant consumers
    - include_for: set of consumers where this skill is relevant
    - category/tags: optional metadata for grouping / filtering
    """

    id: str
    name: str
    description: str
    version: Optional[str] = None
    namespace: str = "public"

    tools: List[SkillToolRef] = field(default_factory=list)
    instruction_paths: SkillInstructionPaths = field(
        default_factory=SkillInstructionPaths
    )
    instruction_text: Optional[str] = None
    instruction_compact_text: Optional[str] = None
    instruction_tokens: Optional[int] = None

    built_in: bool = False
    include_for: List[SkillConsumer] = field(default_factory=list)

    category: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    author: Optional[str] = None
    created: Optional[str] = None
    when_to_use: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    when_to_use: List[str] = field(default_factory=list)

    # ---- helpers ----

    def tool_ids(self) -> List[str]:
        return [t.id for t in self.tools]

    def to_prompt_dict(self, *, consumer: SkillConsumer) -> Dict[str, Any]:
        """
        Shape for the 'skills gallery' in prompts.
        Keep it compact and stable so it cache-hits well.
        """
        return {
            "id": f"{self.namespace}.{self.id}" if self.namespace else self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "instruction_tokens": self.instruction_tokens,
            "category": self.category or "",
            "tags": self.tags,
            "when_to_use": list(self.when_to_use or []),
            "tools": [asdict(t) for t in self.tools],
            "built_in": self.built_in,
            # Consumer-specific hints can be added later if needed
        }


# -------------------- YAML loading --------------------


def _resolve_rel(path_str: str, base: pathlib.Path) -> pathlib.Path:
    """Resolve a path relative to base."""
    p = pathlib.Path(path_str)
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def _split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :]).lstrip()
            return fm, body
    return None, text


def _normalize_include_for(include_for_raw: Any, *, default_all: bool = False) -> List[SkillConsumer]:
    include_for: List[SkillConsumer] = []
    if not include_for_raw:
        return []
    if isinstance(include_for_raw, str):
        include_for_raw = [include_for_raw]
    for c in include_for_raw:
        c = str(c).strip().lower()
        if c:
            include_for.append(c)  # type: ignore[arg-type]
    return include_for


def _normalize_imports(imports_raw: Any) -> List[str]:
    imports: List[str] = []
    if not imports_raw:
        return imports
    if isinstance(imports_raw, str):
        imports_raw = [imports_raw]
    for item in imports_raw:
        s = str(item).strip()
        if not s:
            continue
        if "." not in s:
            s = f"public.{s}"
        imports.append(s)
    return imports


def _normalize_tools(tools_raw: Any, *, sid: str, path: pathlib.Path) -> List[SkillToolRef]:
    tools: List[SkillToolRef] = []
    if isinstance(tools_raw, list):
        for item in tools_raw:
            if isinstance(item, str):
                tools.append(SkillToolRef(id=item.strip() or ""))
            elif isinstance(item, dict):
                tid = str(item.get("id") or "").strip()
                if not tid:
                    continue
                role = item.get("role")
                why = item.get("why") or item.get("reason") or item.get("description")
                tools.append(SkillToolRef(
                    id=tid,
                    role=str(role) if role else None,
                    why=str(why).strip() if why else None,
                ))
            else:
                log.warning("Skill %s: unexpected tools entry %r in %s", sid, item, path)
    return tools


def _load_skill_yaml(path: pathlib.Path) -> SkillSpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Skill YAML {path} must contain a mapping at top level")

    sid = str(raw.get("id") or path.stem)
    name = str(raw.get("name") or sid)
    if name.strip() != sid.strip():
        log.warning("Skill %s: name != id in %s; using id for display name", sid, path)
        name = sid
    desc = str(raw.get("description") or "").strip()

    if not desc:
        log.warning("Skill %s (%s) has empty description", sid, path)

    # ---- instruction paths ----
    instr_conf = raw.get("instruction") or {}
    full_path = None
    compact_path = None

    if isinstance(instr_conf, str):
        # single path used as "full"
        full_path = _resolve_rel(instr_conf, base=path.parent)
    elif isinstance(instr_conf, dict):
        full_str = instr_conf.get("full")
        compact_str = instr_conf.get("compact")
        if full_str:
            full_path = _resolve_rel(str(full_str), base=path.parent)
        if compact_str:
            compact_path = _resolve_rel(str(compact_str), base=path.parent)

    instr_paths = SkillInstructionPaths(full=full_path, compact=compact_path)

    # ---- tool refs ----
    tools_raw = raw.get("tools") or []
    tools = _normalize_tools(tools_raw, sid=sid, path=path)

    # ---- flags & metadata ----
    built_in = bool(raw.get("built_in", False))

    include_for = _normalize_include_for(raw.get("include_for") or [])

    namespace = str(raw.get("namespace") or "public").strip()
    category = raw.get("category")
    if category is not None:
        category = str(category)

    tags_raw = raw.get("tags") or []
    tags: List[str] = []
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]
    for t in tags_raw:
        s = str(t).strip()
        if s:
            tags.append(s)

    when_raw = raw.get("when_to_use") or raw.get("when_to_use_lines") or []
    when_to_use: List[str] = []
    if isinstance(when_raw, str):
        when_raw = [line for line in when_raw.splitlines() if line.strip()]
    for item in when_raw:
        s = str(item).strip()
        if s.startswith("- "):
            s = s[2:].strip()
        if s:
            when_to_use.append(s)

    instr_text = str(raw.get("instruction_text")).strip() if raw.get("instruction_text") else None
    instr_tokens = None
    if instr_text and estimate_tokens:
        try:
            instr_tokens = estimate_tokens(instr_text)
        except Exception:
            instr_tokens = None
    return SkillSpec(
        id=sid,
        name=name,
        description=desc,
        version=str(raw.get("version")) if raw.get("version") is not None else None,
        namespace=namespace or "public",
        tools=tools,
        instruction_paths=instr_paths,
        instruction_text=instr_text,
        instruction_compact_text=str(raw.get("instruction_compact_text")).strip() if raw.get("instruction_compact_text") else None,
        instruction_tokens=instr_tokens,
        built_in=built_in,
        include_for=include_for,
        category=category,
        tags=tags,
        author=str(raw.get("author")).strip() if raw.get("author") else None,
        created=str(raw.get("created")).strip() if raw.get("created") else None,
        when_to_use=when_to_use,
        imports=_normalize_imports(raw.get("import") or raw.get("imports")),
    )


def _load_skill_markdown(path: pathlib.Path) -> SkillSpec:
    text = path.read_text(encoding="utf-8")
    fm_text, body = _split_frontmatter(text)
    raw = yaml.safe_load(fm_text) if fm_text else {}
    if not isinstance(raw, dict):
        raw = {}

    folder_name = path.parent.name
    namespace_folder = path.parent.parent.name
    name = str(raw.get("name") or folder_name or path.stem).strip()
    sid = str(raw.get("id") or raw.get("name") or folder_name or path.stem).strip()
    if name.strip() != sid.strip():
        log.warning("Skill %s: name != id in %s; using id for display name", sid, path)
        name = sid
    desc = str(raw.get("description") or "").strip()
    if not desc:
        log.warning("Skill %s (%s) has empty description", sid, path)

    include_for = _normalize_include_for(raw.get("include_for") or [], default_all=True)
    built_in = bool(raw.get("built_in", True))

    namespace = str(raw.get("namespace") or namespace_folder or "public").strip()
    category = raw.get("category")
    if category is not None:
        category = str(category)

    tags_raw = raw.get("tags") or []
    tags: List[str] = []
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]
    for t in tags_raw:
        s = str(t).strip()
        if s:
            tags.append(s)

    when_raw = raw.get("when_to_use") or raw.get("when_to_use_lines") or []
    when_to_use: List[str] = []
    if isinstance(when_raw, str):
        when_raw = [line for line in when_raw.splitlines() if line.strip()]
    for item in when_raw:
        s = str(item).strip()
        if s.startswith("- "):
            s = s[2:].strip()
        if s:
            when_to_use.append(s)

    compact_text = None
    compact_path = path.parent / "compact.md"
    if compact_path.exists():
        compact_text = compact_path.read_text(encoding="utf-8").strip()

    instr_tokens = None
    if body and estimate_tokens:
        try:
            instr_tokens = estimate_tokens(body)
        except Exception:
            instr_tokens = None

    return SkillSpec(
        id=sid,
        name=name,
        description=desc,
        version=str(raw.get("version")).strip() if raw.get("version") else None,
        namespace=namespace or "public",
        tools=[],
        instruction_paths=SkillInstructionPaths(full=None, compact=compact_path if compact_text else None),
        instruction_text=body.strip(),
        instruction_compact_text=compact_text,
        instruction_tokens=instr_tokens,
        built_in=built_in,
        include_for=include_for,
        category=category,
        tags=tags,
        author=str(raw.get("author")).strip() if raw.get("author") else None,
        created=str(raw.get("created")).strip() if raw.get("created") else None,
        when_to_use=when_to_use,
        imports=_normalize_imports(raw.get("import") or raw.get("imports")),
    )


def _load_skill_tools_yaml(path: pathlib.Path, *, sid: str) -> List[SkillToolRef]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return []
    tools_raw = raw.get("tools") or raw.get("tool_ids") or []
    return _normalize_tools(tools_raw, sid=sid, path=path)


def _normalize_descriptor(
        desc: Optional[Dict[str, Any]],
        *,
        bundle_root: Optional[pathlib.Path] = None,
) -> Dict[str, Any]:
    data = dict(desc or {})
    if not isinstance(data, dict):
        data = {}
    root_val = data.get("custom_skills_root") or data.get("CUSTOM_SKILLS_ROOT")
    if isinstance(root_val, str) and root_val.strip():
        root = pathlib.Path(root_val.strip())
        if not root.is_absolute() and bundle_root is not None:
            root = (bundle_root / root).resolve()
        data["custom_skills_root"] = str(root)
    else:
        data["custom_skills_root"] = None
    cfg = data.get("agents_config") or data.get("AGENTS_CONFIG")
    if isinstance(cfg, dict):
        data["agents_config"] = cfg
    else:
        data["agents_config"] = {}
    if bundle_root is not None:
        data["bundle_root"] = str(bundle_root)
    return data


class SkillsSubsystem:
    """
    Single place to resolve skill descriptors, load skill files, and provide
    consumer-facing helpers for prompts and instruction injection.
    """

    def __init__(
            self,
            *,
            descriptor: Optional[Dict[str, Any]] = None,
            bundle_root: Optional[pathlib.Path] = None,
    ):
        self.bundle_root = bundle_root
        self.descriptor = _normalize_descriptor(descriptor, bundle_root=bundle_root)
        custom_root = self.descriptor.get("custom_skills_root")
        self.custom_skills_root = pathlib.Path(custom_root) if isinstance(custom_root, str) and custom_root else None
        self.agents_config = self.descriptor.get("agents_config") or {}
        self._registry_cache: Optional[Dict[str, SkillSpec]] = None

    def export_runtime_globals(self) -> Dict[str, Any]:
        return {"SKILLS_DESCRIPTOR": self.descriptor}

    def clear_cache(self) -> None:
        self._registry_cache = None

    def _iter_skill_files(self) -> Iterable[pathlib.Path]:
        roots: List[pathlib.Path] = [BUILTIN_SKILLS_ROOT / "skills"]
        if self.custom_skills_root:
            roots.append(self.custom_skills_root)
        files: List[pathlib.Path] = []
        for root in roots:
            if not root.exists():
                continue
            files.extend([p for p in root.glob("*/*/SKILL.md") if p.is_file()])
            files.extend([p for p in root.glob("*/*/skill.y*ml") if p.is_file()])
            files.extend([p for p in root.glob("*/*/*.y*ml") if p.is_file() and p.name.lower() not in ("tools.yaml", "tools.yml")])
        if not files:
            log.warning("Skills directories not found; no skills loaded")
        return sorted(set(files))

    def get_skill_registry(self) -> Dict[str, SkillSpec]:
        if self._registry_cache is not None:
            return self._registry_cache
        registry: Dict[str, SkillSpec] = {}
        for path in self._iter_skill_files():
            try:
                if path.suffix.lower() == ".md":
                    s = _load_skill_markdown(path)
                    tools_path = path.parent / "tools.yaml"
                    extra_tools = _load_skill_tools_yaml(tools_path, sid=s.id)
                    if extra_tools:
                        s.tools = extra_tools
                else:
                    s = _load_skill_yaml(path)
            except Exception as e:
                log.error("Failed to load skill from %s: %s", path, e)
                continue

            key = f"{s.namespace}.{s.id}" if s.namespace else s.id
            if key in registry:
                log.warning(
                    "Duplicate skill id %s from %s (existing from %s) – overriding",
                    key,
                    path,
                    registry[key],
                )
            registry[key] = s

        log.info("Loaded %d skills: %s", len(registry), ", ".join(sorted(registry.keys())))
        self._registry_cache = registry
        return registry


def set_active_skills_subsystem(subsystem: SkillsSubsystem) -> None:
    SKILLS_SUBSYSTEM_CV.set(subsystem)
    SKILLS_DESCRIPTOR_CV.set(subsystem.descriptor)


def set_skills_descriptor(
        descriptor: Optional[Dict[str, Any]],
        *,
        bundle_root: Optional[pathlib.Path] = None,
) -> SkillsSubsystem:
    subsystem = SkillsSubsystem(descriptor=descriptor, bundle_root=bundle_root)
    set_active_skills_subsystem(subsystem)
    return subsystem


def get_active_skills_subsystem() -> SkillsSubsystem:
    existing = SKILLS_SUBSYSTEM_CV.get()
    if existing is not None:
        return existing
    desc = SKILLS_DESCRIPTOR_CV.get()
    subsystem = SkillsSubsystem(descriptor=desc)
    set_active_skills_subsystem(subsystem)
    return subsystem


def clear_skill_registry_cache() -> None:
    get_active_skills_subsystem().clear_cache()


# -------------------- Public helpers --------------------


def get_skill(skill_id: str) -> Optional[SkillSpec]:
    sid = str(skill_id or "").strip()
    if not sid:
        return None
    reg = get_active_skills_subsystem().get_skill_registry()
    if sid in reg:
        return reg.get(sid)
    if "." not in sid:
        return reg.get(f"public.{sid}")
    return None


def skills_for_consumer(
        consumer: SkillConsumer,
        *,
        include_builtins: bool = True,
        include_non_builtins: bool = True,
        include_internal: bool = False,
        include_public: bool = True,
        include_custom: bool = True,
) -> List[SkillSpec]:
    """
    Return skills relevant for a given consumer type.

    - consumer: arbitrary string id (only enforced if present in agents_config)
    - include_builtins: include skills with built_in=True
    - include_non_builtins: include skills with built_in=False
    """
    enabled = None
    disabled = None
    subsystem = get_active_skills_subsystem()
    agents_config = subsystem.agents_config or {}
    if isinstance(agents_config, dict):
        cfg = agents_config.get(consumer) or {}
        enabled = cfg.get("enabled")
        disabled = cfg.get("disabled")
    enabled_set: Optional[Set[str]] = None
    disabled_set: Optional[Set[str]] = None
    enabled_ns: Optional[Set[str]] = None
    disabled_ns: Optional[Set[str]] = None
    enabled_glob: Optional[List[str]] = None
    disabled_glob: Optional[List[str]] = None

    def _normalize_ns_patterns(items: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for raw in items:
            s = str(raw or "").strip()
            if not s or not s.endswith(".*"):
                continue
            ns = s[:-2].strip()
            if ns:
                out.add(ns)
        return out

    if enabled:
        enabled_set = set()
        enabled_ns = _normalize_ns_patterns(enabled)
        enabled_glob = [str(r).strip() for r in enabled if isinstance(r, str) and "*" in r and not r.endswith(".*")]
        for ref in enabled:
            resolved = resolve_skill_ref(ref)
            if resolved:
                enabled_set.add(resolved)
    elif disabled:
        disabled_set = set()
        disabled_ns = _normalize_ns_patterns(disabled)
        disabled_glob = [str(r).strip() for r in disabled if isinstance(r, str) and "*" in r and not r.endswith(".*")]
        for ref in disabled:
            resolved = resolve_skill_ref(ref)
            if resolved:
                disabled_set.add(resolved)

    out: List[SkillSpec] = []
    for s in subsystem.get_skill_registry().values():
        if s.namespace == "internal" and not include_internal:
            continue
        if s.namespace == "public" and not include_public:
            continue
        if s.namespace == "custom" and not include_custom:
            continue
        if isinstance(agents_config, dict) and consumer in agents_config:
            if s.include_for and consumer not in s.include_for:
                continue
        if s.built_in and not include_builtins:
            continue
        if (not s.built_in) and not include_non_builtins:
            continue
        if enabled_set is not None:
            full_id = f"{s.namespace}.{s.id}" if s.namespace else s.id
            if enabled_ns and s.namespace not in enabled_ns and full_id not in enabled_set:
                if not enabled_glob or not any(fnmatch.fnmatchcase(full_id, pat) for pat in enabled_glob):
                    continue
            if enabled_glob and any(fnmatch.fnmatchcase(full_id, pat) for pat in enabled_glob):
                pass
            elif enabled_ns and s.namespace in enabled_ns:
                pass
            elif full_id in enabled_set:
                pass
            else:
                continue
        if disabled_set is not None:
            full_id = f"{s.namespace}.{s.id}" if s.namespace else s.id
            if disabled_ns and s.namespace in disabled_ns:
                continue
            if disabled_glob and any(fnmatch.fnmatchcase(full_id, pat) for pat in disabled_glob):
                continue
            if full_id in disabled_set:
                continue
        out.append(s)
    return sorted(out, key=lambda s: f"{s.namespace}.{s.id}")


def skills_catalog_for_prompt(
        *,
        consumer: SkillConsumer,
        include_builtins: bool = True,
        include_non_builtins: bool = True,
        include_internal: bool = False,
        include_public: bool = True,
        include_custom: bool = True,
        tool_catalog: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Shape for embedding into prompts as a 'skills gallery'.

    Example element:
      {
        "id": "web_search",
        "name": "Web search & evidence gathering",
        "description": "...",
        "category": "research",
        "tags": ["built_in", "discovery"],
        "tools": [{"id":"web_tools.web_search","role":"search"}, ...],
        "built_in": true,
        "include_for": ["coordinator","react_decision"]
      }
    """
    skills = skills_for_consumer(
        consumer,
        include_builtins=include_builtins,
        include_non_builtins=include_non_builtins,
        include_internal=include_internal,
        include_public=include_public,
        include_custom=include_custom,
    )
    tool_map: Dict[str, Dict[str, Any]] = {}
    if tool_catalog:
        for t in tool_catalog:
            tid = (t or {}).get("id")
            if isinstance(tid, str) and tid:
                tool_map[tid] = t

    out: List[Dict[str, Any]] = []
    for s in skills:
        rec = s.to_prompt_dict(consumer=consumer)
        if tool_map and rec.get("tools"):
            enriched = []
            for t in rec["tools"]:
                tid = t.get("id")
                info = tool_map.get(tid) if isinstance(tid, str) else None
                if info and info.get("purpose"):
                    t = dict(t)
                    t["purpose"] = info.get("purpose")
                enriched.append(t)
            rec["tools"] = enriched
        out.append(rec)
    return out


def build_skill_short_id_map(
        *,
        consumer: SkillConsumer,
        include_internal: bool = False,
        include_public: bool = True,
        include_custom: bool = True,
) -> Dict[str, str]:
    skills = skills_for_consumer(
        consumer,
        include_internal=include_internal,
        include_public=include_public,
        include_custom=include_custom,
    )
    short_map: Dict[str, str] = {}
    for i, s in enumerate(skills, start=1):
        short_id = f"SK{i}"
        full_id = f"{s.namespace}.{s.id}" if s.namespace else s.id
        short_map[short_id] = full_id
    return short_map


def _wrap_lines(text: str, width: int = 76, indent: str = "   ") -> List[str]:
    if not text:
        return []
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent).splitlines()


def skills_gallery_text(
        *,
        consumer: SkillConsumer,
        tool_catalog: Optional[List[Dict[str, Any]]] = None,
) -> str:
    skills = skills_for_consumer(consumer=consumer)
    if not skills:
        return "[SKILL CATALOG]\n(no skills)"

    tool_map: Dict[str, Dict[str, Any]] = {}
    if tool_catalog:
        for t in tool_catalog:
            tid = (t or {}).get("id")
            if isinstance(tid, str) and tid:
                tool_map[tid] = t

    lines: List[str] = [
        "[SKILL CATALOG]",
        "The following skills are available to enhance agents capabilities. "
        "Use `sk:<NAMESPACE>.<SKILL_ID>` to load a skill's full documentation when needed. For example, sk:public.pdf-press",
        "",
        "═" * 79,
        "",
    ]

    for idx, spec in enumerate(skills, start=1):
        s = spec.to_prompt_dict(consumer=consumer)
        name = s.get("name") or spec.id or "unknown"
        sid = s.get("id") or f"{spec.namespace}.{spec.id}"
        short_id = f"SK{idx}"
        built_in = " [Built-in]" if s.get("built_in") else ""
        version = s.get("version") or ""
        ver_txt = f" v{version}" if version else ""
        lines.append(f"📦 [{short_id}] {sid}{built_in}{ver_txt}")

        category = s.get("category") or ""
        tags = s.get("tags") or []
        if category or tags:
            tags_txt = ", ".join(tags) if tags else ""
            line = "   " + " • ".join([p for p in [category, tags_txt] if p])
            lines.append(line)
            lines.append("")

        desc = s.get("description") or ""
        if desc:
            lines.extend(_wrap_lines(desc))
            lines.append("")
        inst_tokens = s.get("instruction_tokens")
        if inst_tokens is not None:
            lines.append(f"   🧮 Instruction size: {inst_tokens} tok")
            lines.append("")

        tools = s.get("tools") or []
        if tools:
            lines.append("   🛠️  Tools:")
            for t in tools:
                tid = t.get("id") or "unknown"
                role = t.get("role")
                role_txt = f" ({role})" if role else ""
                lines.append(f"       • {tid}{role_txt}")
                info = tool_map.get(tid, {})
                purpose = info.get("purpose") if isinstance(info, dict) else None
                why = t.get("why") or purpose or ""
                if why:
                    lines.extend(_wrap_lines(why, indent="         → "))
            lines.append("")

        lines.append(f"   📂 Path: skills.{sid}")

        when_to_use = s.get("when_to_use") or []
        if when_to_use:
            lines.append("")
            lines.append("   ⚡ When to use:")
            for item in when_to_use:
                lines.append(f"      • {item}")

        lines.append("")
        lines.append("━" * 77)
        lines.append("")

    return "\n".join([l for l in lines if l is not None])


def validate_skill_tools(known_tool_ids: Iterable[str]) -> List[str]:
    """
    Optional sanity check: ensure all skill tool ids exist in the tool catalog.

    Returns a list of human-readable warning strings (no exception).
    You can call this once after ToolSubsystem initialization.
    """
    known = set(known_tool_ids)
    warnings: List[str] = []

    for skill in get_active_skills_subsystem().get_skill_registry().values():
        for t in skill.tools:
            if t.id not in known:
                msg = f"Skill '{skill.id}' references unknown tool id '{t.id}'"
                log.warning(msg)
                warnings.append(msg)
    return warnings


def normalize_skill_ids(skill_ids: Optional[Iterable[str]]) -> List[str]:
    """Normalize, de-duplicate, and filter skill ids against the registry."""
    if not skill_ids:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for raw in skill_ids:
        sid = resolve_skill_ref(str(raw or "").strip())
        if not sid:
            log.warning("Unknown skill id requested: %s", raw)
            continue
        if sid in seen:
            continue
        out.append(sid)
        seen.add(sid)
    return out


def import_skillset(
        skill_ids: Optional[Iterable[str]],
        *,
        short_id_map: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    Resolve skill refs, include imports, and de-duplicate with cycle safety.

    Returns a stable ordered list of unique skill ids.
    """
    if not skill_ids:
        return []
    ordered: List[str] = []
    seen: Set[str] = set()
    visiting: Set[str] = set()

    def _add(sid: str) -> None:
        if sid in seen:
            return
        if sid in visiting:
            log.warning("Skill import cycle detected at '%s'", sid)
            return
        visiting.add(sid)
        spec = get_skill(sid)
        if not spec:
            visiting.remove(sid)
            return
        for dep in spec.imports or []:
            dep_id = resolve_skill_ref(dep, short_id_map=short_id_map)
            if dep_id:
                _add(dep_id)
            else:
                log.warning("Unknown skill import '%s' from '%s'", dep, sid)
        visiting.remove(sid)
        seen.add(sid)
        ordered.append(sid)

    for raw in skill_ids:
        sid = resolve_skill_ref(str(raw or "").strip(), short_id_map=short_id_map)
        if not sid:
            log.warning("Unknown skill id requested: %s", raw)
            continue
        _add(sid)
    return ordered


def resolve_skill_ref(
        ref: str,
        *,
        short_id_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    if not ref:
        return None
    s = str(ref).strip()
    if not s:
        return None
    if short_id_map and s in short_id_map:
        return short_id_map[s]
    if s.startswith("skills."):
        s = s[len("skills."):]
        if s.startswith("skills."):
            s = s[len("skills."):]
    reg = get_active_skills_subsystem().get_skill_registry()
    if s in reg:
        return s
    if "." not in s:
        s = f"public.{s}"
    return s if s in reg else None


def _read_instruction_text(path: Optional[pathlib.Path]) -> str:
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        log.warning("Failed to read skill instruction at %s: %s", path, e)
        return ""


def build_skills_instruction_block(
        skill_ids: Optional[Iterable[str]],
        *,
        variant: Literal["full", "compact"] = "full",
        header: str = "ACTIVE SKILLS",
) -> str:
    """
    Render skill instructions as a single block to be inserted into prompts/journals.
    """
    normalized = import_skillset(skill_ids)
    if not normalized:
        return ""
    blocks: List[str] = []
    for sid in normalized:
        spec = get_skill(sid)
        if not spec:
            continue
        instr_text = ""
        if variant == "compact" and spec.instruction_compact_text:
            instr_text = spec.instruction_compact_text.strip()
        elif spec.instruction_text:
            instr_text = spec.instruction_text.strip()
        if not instr_text:
            instr_path = spec.instruction_paths.compact if variant == "compact" else spec.instruction_paths.full
            instr_text = _read_instruction_text(instr_path)
        if not instr_text:
            continue
        blocks.append(
            "\n".join([
                f"## Skill: {spec.name} ({spec.namespace}.{spec.id})",
                instr_text,
            ])
        )
    if not blocks:
        return ""
    return "\n".join([
        f"[{header}]",
        *blocks,
    ])


def top_level_skill_ids_for_consumer(consumer: SkillConsumer) -> List[str]:
    skills = skills_for_consumer(consumer=consumer)
    imported: set[str] = set()
    for s in skills:
        for dep in getattr(s, "imports", []) or []:
            resolved = resolve_skill_ref(dep)
            if resolved:
                imported.add(resolved)
    out: List[str] = []
    seen: set[str] = set()
    for s in skills:
        full_id = f"{s.namespace}.{s.id}" if s.namespace else s.id
        if full_id in imported or full_id in seen:
            continue
        out.append(full_id)
        seen.add(full_id)
    return out
