from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Dict, Optional, Iterable, Tuple, List

DEFAULT_TREE_MAX_CHARS = 4000
DEFAULT_LOG_MAX_CHARS = 4000
USER_CODE_MARKER = "# === USER CODE START ==="
TRACEBACK_REMAP_ENV = "EXEC_TRACEBACK_REMAP"


def read_log_tail(path: Path, max_chars: int = DEFAULT_LOG_MAX_CHARS) -> str:
    try:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
    except Exception:
        return ""


def extract_exec_segment(text: str, exec_id: Optional[str]) -> str:
    if not text or not exec_id:
        return text or ""
    marker = f"===== EXECUTION {exec_id} START"
    idx = text.rfind(marker)
    if idx == -1:
        return text
    return text[idx:]


def find_user_code_start_line(main_path: Path) -> Optional[int]:
    try:
        if not main_path.exists():
            return None
        lines = main_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for idx, line in enumerate(lines, start=1):
            if line.strip() == USER_CODE_MARKER:
                return idx + 1
    except Exception:
        return None
    return None


def remap_traceback_line_numbers(text: str, user_code_start_line: Optional[int]) -> str:
    """
    Remap traceback line numbers in main.py to the original user code line numbers
    by subtracting the injected header offset.
    """
    if not text or not user_code_start_line:
        return text or ""
    if os.environ.get(TRACEBACK_REMAP_ENV, "1").strip().lower() in {"0", "false", "no"}:
        return text
    offset = user_code_start_line - 1
    if offset <= 0:
        return text

    def _repl(match: re.Match) -> str:
        prefix = match.group(1)
        line_no = int(match.group(2))
        if line_no >= user_code_start_line:
            return f"{prefix}{line_no - offset}"
        return f"{prefix}{line_no}"

    return re.sub(r'(File "[^"]*main\\.py", line )(\\d+)', _repl, text)


def _default_infra_sources(log_dir: Path) -> List[Tuple[str, str]]:
    sources: List[Tuple[str, str]] = []
    if not log_dir.exists():
        return sources
    for path in sorted(log_dir.glob("*.log")):
        name = path.name
        if name in {"user.log", "infra.log"}:
            continue
        label = name.replace(".log", "")
        sources.append((name, label))
    return sources


def merge_infra_logs(
    *,
    log_dir: Path,
    exec_id: Optional[str],
    sources: Optional[Iterable[Tuple[str, str]]] = None,
    max_chars: int = DEFAULT_LOG_MAX_CHARS,
) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    if not exec_id:
        return ""
    infra_path = log_dir / "infra.log"
    if infra_path.exists():
        existing = read_log_tail(infra_path, max_chars=max_chars)
        if f"===== EXECUTION {exec_id} START" in existing:
            return extract_exec_segment(existing, exec_id)
    blocks: list[str] = []
    use_sources = list(sources) if sources is not None else _default_infra_sources(log_dir)
    for fname, label in use_sources:
        if not fname:
            continue
        path = log_dir / fname
        if not path.exists():
            continue
        text = read_log_tail(path, max_chars=max_chars)
        seg = extract_exec_segment(text, exec_id)
        if seg:
            # Remove per-source execution banners; we add a single banner for the merged log.
            seg = "\n".join(
                ln for ln in seg.splitlines()
                if not ln.startswith("===== EXECUTION ")
            ).strip()
        if not seg.strip():
            continue
        blocks.append(f"[{label}]\n{seg.strip()}")
    if not blocks:
        return ""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    eid = exec_id or "unknown"
    header = f"\n===== EXECUTION {eid} START {ts} =====\n"
    merged = "\n\n".join(blocks) + "\n"
    try:
        with open(infra_path, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(merged)
    except Exception:
        pass
    return merged

def extract_error_lines(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    hits = [ln for ln in lines if re.search(r"\\bERROR\\b", ln, flags=re.IGNORECASE)]
    if not hits:
        return ""
    return "\n".join(hits)

def extract_traceback_blocks(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        if "Traceback" in lines[i]:
            block = [lines[i]]
            i += 1
            while i < len(lines):
                if lines[i].startswith("===== EXECUTION"):
                    break
                if lines[i].startswith("[") and lines[i].endswith("]"):
                    break
                block.append(lines[i])
                i += 1
            blocks.append("\n".join(block).strip())
            continue
        i += 1
    return "\n\n".join([b for b in blocks if b])


_LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "FATAL": 50,
}


def filter_log_by_level(
    text: str,
    *,
    level: str = "ERROR",
    min_level: bool = True,
    include_untagged: bool = False,
) -> str:
    """
    Filter log lines by level. Supports exact match or min-level filtering.
    Lines without a level token are excluded unless include_untagged=True.
    """
    if not text:
        return ""
    target = _LEVEL_ORDER.get((level or "ERROR").upper(), 40)
    out: list[str] = []
    for line in text.splitlines():
        m = re.search(r"\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b", line)
        if not m:
            if include_untagged:
                out.append(line)
            continue
        lvl = _LEVEL_ORDER.get(m.group(1).upper(), 0)
        if (min_level and lvl >= target) or (not min_level and lvl == target):
            out.append(line)
    return "\n".join(out)


def build_tree(root: Path, *, skip_dirs: set[str], max_chars: int = DEFAULT_TREE_MAX_CHARS) -> str:
    if not root.exists():
        return "<empty>"
    lines: list[str] = []
    root = root.resolve()
    for cur, dirs, files in os.walk(root):
        rel = Path(cur).relative_to(root)
        if rel.parts and rel.parts[0] in skip_dirs:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        indent = "  " * len(rel.parts)
        if rel.parts:
            lines.append(f"{indent}{rel.name}/")
        for name in sorted(files):
            if name == "delta_aggregates.json":
                continue
            lines.append(f"{indent}  {name}")
        if sum(len(x) + 1 for x in lines) > max_chars:
            lines.append("  ... (truncated)")
            break
    return "\n".join(lines)


def collect_exec_diagnostics(
    *,
    sandbox_root: Path,
    outdir: Path,
    exec_id: Optional[str],
    tree_max_chars: int = DEFAULT_TREE_MAX_CHARS,
    log_max_chars: int = DEFAULT_LOG_MAX_CHARS,
) -> Dict[str, str]:
    log_dir = outdir / "logs"
    infra_log_max = max(log_max_chars, 12000)
    merge_infra_logs(log_dir=log_dir, exec_id=exec_id, max_chars=infra_log_max)
    tree = build_tree(sandbox_root, skip_dirs={"logs"}, max_chars=tree_max_chars)
    user_code_start_line = find_user_code_start_line(sandbox_root / "work" / "main.py")
    if not user_code_start_line:
        user_code_start_line = find_user_code_start_line(outdir / "main.py")
    user_out = extract_exec_segment(
        read_log_tail(log_dir / "user.log", max_chars=log_max_chars),
        exec_id,
    )
    infra_path = log_dir / "infra.log"
    runtime_err = extract_exec_segment(
        read_log_tail(infra_path if infra_path.exists() else (outdir / "logs" / "runtime.err.log"),
                      max_chars=infra_log_max),
        exec_id,
    )
    if user_code_start_line:
        user_out = remap_traceback_line_numbers(user_out, user_code_start_line)
        runtime_err = remap_traceback_line_numbers(runtime_err, user_code_start_line)
    user_error_lines = extract_error_lines(user_out)
    runtime_error_lines = extract_error_lines(runtime_err)
    user_tracebacks = extract_traceback_blocks(user_out)
    runtime_tracebacks = extract_traceback_blocks(runtime_err)
    has_err = bool(user_error_lines or user_tracebacks)
    runtime_has_err = bool(runtime_error_lines or runtime_tracebacks)
    return {
        "tree": tree,
        "error_log": "",
        "info_log": user_out,
        "error_lines": user_error_lines,
        "tracebacks": user_tracebacks,
        "runtime_error_log": runtime_err,
        "runtime_error_lines": runtime_error_lines,
        "runtime_tracebacks": runtime_tracebacks,
        "has_error": "1" if has_err else "0",
        "runtime_has_error": "1" if runtime_has_err else "0",
    }
