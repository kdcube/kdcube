# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.diagnose import (
    collect_exec_diagnostics as _collect,
)


def collect_exec_diagnostics(
    *,
    sandbox_root: Path,
    outdir: Path,
    exec_id: str,
) -> Dict[str, str]:
    return _collect(
        sandbox_root=sandbox_root,
        outdir=outdir,
        exec_id=exec_id,
    )


def scenarios():
    return [s.label for s in SCENARIOS]


@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    label: str
    description: str


SCENARIOS: List[ScenarioSpec] = [
    ScenarioSpec("0", "0. Happy path (writes file + note)", "writes expected output"),
    ScenarioSpec("1", "1. Runtime timeout (no progress)", "infinite loop without output"),
    ScenarioSpec("2", "2. Runtime timeout (with progress)", "prints then loops forever"),
    ScenarioSpec("3", "3. Program crash (exception)", "raises a runtime error"),
    ScenarioSpec("4", "4. Program partial success", "produces only часть of contract"),
    ScenarioSpec("5", "5. Program MemoryError", "raises MemoryError explicitly"),
    ScenarioSpec("6", "6. Program recursion error", "infinite recursion"),
    ScenarioSpec("7", "7. Program timeout (sleep)", "sleeps past timeout"),
    ScenarioSpec("8", "8. Program infinite loop", "busy loop with no sleep"),
    ScenarioSpec("9", "9. Program output then crash (print)", "prints output then raises"),
    ScenarioSpec("10", "10. Program logging INFO+ERROR", "logs info and error via logging"),
    ScenarioSpec("11", "11. Program writes file then crashes", "writes output then raises"),
    ScenarioSpec("12", "12. Side-effects (no contract)", "runs without contract, diff out/"),
]


def select_scenario(text: str) -> ScenarioSpec:
    raw = (text or "").strip()
    if raw:
        for spec in SCENARIOS:
            if raw.startswith(f"{spec.id}.") or raw == spec.id or raw.lower().startswith(f"scenario {spec.id}"):
                return spec
    return SCENARIOS[0]


def build_scenario(*, turn_id: str, scenario: ScenarioSpec) -> Dict[str, object]:
    contract = [
        {
            "filename": f"{turn_id}/files/hello-iso-runtime.txt",
            "description": "Sample output produced by iso-runtime execution.",
        }
    ]
    timeout_s = 10
    lines = [
        "from pathlib import Path",
        "import os, time",
        "",
        "out_dir = Path(os.environ['OUTPUT_DIR'])",
        f"out_path = out_dir / '{turn_id}/files/hello-iso-runtime.txt'",
        "out_path.parent.mkdir(parents=True, exist_ok=True)",
    ]
    if scenario.id == "0":
        lines += [
            "out_path.write_text('hello from iso-runtime', encoding='utf-8')",
            "await agent_io_tools.tool_call(",
            "    fn=local_tools.write_note,",
            "    params={'text': 'note from iso-runtime'},",
            "    call_reason='Write a simple note file',",
            "    tool_id='local_tools.write_note'",
            ")",
        ]
    elif scenario.id == "1":
        lines += ["while True: pass"]
        timeout_s = 3
    elif scenario.id == "2":
        lines += ["print('progress before timeout')", "while True: pass"]
        timeout_s = 3
    elif scenario.id == "3":
        lines += ["raise RuntimeError('simulated program crash')"]
    elif scenario.id == "4":
        contract = contract + [
            {
                "filename": f"{turn_id}/files/missing-output.txt",
                "description": "This file is intentionally missing.",
            }
        ]
        lines += ["out_path.write_text('partial output', encoding='utf-8')"]
    elif scenario.id == "5":
        lines += ["raise MemoryError('simulated memory error')"]
    elif scenario.id == "6":
        lines += [
            "def _recurse():",
            "    return _recurse()",
            "_recurse()",
        ]
    elif scenario.id == "7":
        lines += ["print('sleeping...')", "time.sleep(999)"]
        timeout_s = 3
    elif scenario.id == "8":
        lines += ["print('looping...')", "while True: pass"]
        timeout_s = 3
    elif scenario.id == "9":
        lines += [
            "print('hello from print before crash')",
            "raise RuntimeError('simulated crash after print')",
        ]
    elif scenario.id == "10":
        lines += [
            "import logging",
            "out_path.write_text('log-only success', encoding='utf-8')",
            "logger = logging.getLogger('user')",
            "logger.info('info from program logger')",
            "logger.error('error from program logger')",
        ]
    elif scenario.id == "11":
        lines += [
            "out_path.write_text('written before crash', encoding='utf-8')",
            "raise RuntimeError('simulated crash after write')",
        ]
    elif scenario.id == "12":
        contract = []
        lines += [
            "out_path.write_text('side-effects output', encoding='utf-8')",
            "extra_path = out_dir / f'{turn_id}/files/extra-side-effect.txt'",
            "extra_path.parent.mkdir(parents=True, exist_ok=True)",
            "extra_path.write_text('another output', encoding='utf-8')",
        ]
    return {
        "contract": contract,
        "timeout_s": timeout_s,
        "code": "\n".join(lines),
        "use_contract": scenario.id != "12",
    }
