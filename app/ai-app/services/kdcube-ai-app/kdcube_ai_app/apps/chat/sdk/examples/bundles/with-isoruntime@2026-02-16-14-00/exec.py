# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── exec.py ──
# Scenario definitions and dynamic code generation for iso-runtime testing.
#
# This file defines 13 test scenarios that simulate different execution outcomes:
#   - Happy path, timeouts, crashes, memory errors, partial results, side-effects, etc.
#
# Each scenario produces:
#   - Python source code to be executed in the sandbox
#   - An output contract (list of expected output files)
#   - A timeout value
#
# Flow:
#   1. select_scenario(user_text) — parses user message to pick a scenario
#   2. build_scenario(turn_id, scenario) — generates code + contract for it
#   3. The generated code is passed to exec_contract / exec_side_effects
#      which delegate to run_exec_tool() → _InProcessRuntime
#
# Note: the generated code references `agent_io_tools` and `local_tools` —
# these are NOT imported by the code itself. Instead, _InProcessRuntime's
# injected header auto-imports all tool modules from tools_descriptor.py
# and binds them into the script's global namespace before execution.

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
    """Thin wrapper around the SDK diagnostic collector (reads logs, finds errors)."""
    return _collect(
        sandbox_root=sandbox_root,
        outdir=outdir,
        exec_id=exec_id,
    )


def scenarios():
    """Return scenario labels for UI suggestions."""
    return [s.label for s in SCENARIOS]


@dataclass(frozen=True)
class ScenarioSpec:
    """Immutable definition of a test scenario."""
    id: str
    label: str
    description: str


# All available test scenarios — each exercises a different execution path
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
    ScenarioSpec("13", "13. Fargate happy path", "writes expected output using bundle-configured Fargate runtime"),
]


def select_scenario(text: str) -> ScenarioSpec:
    """
    Parse user text to pick a scenario. Supports formats:
      "0", "0.", "scenario 0", etc. Falls back to scenario 0 (happy path).
    """
    raw = (text or "").strip()
    if raw:
        for spec in SCENARIOS:
            if raw.startswith(f"{spec.id}.") or raw == spec.id or raw.lower().startswith(f"scenario {spec.id}"):
                return spec
    return SCENARIOS[0]


def build_scenario(*, turn_id: str, scenario: ScenarioSpec) -> Dict[str, object]:
    """
    Generate Python source code + output contract for a given scenario.

    Returns dict with keys:
      - "code":         Python source code string to execute
      - "contract":     list of expected output files [{filename, description}]
      - "timeout_s":    execution timeout in seconds
      - "use_contract": whether to use contract-based execution (False for side-effects mode)
    """
    # Default contract: one expected output file
    contract = [
        {
            "filename": f"{turn_id}/files/hello-iso-runtime.txt",
            "description": "Sample output produced by iso-runtime execution.",
        }
    ]
    timeout_s = 10

    # Common preamble: all scenarios start with these lines
    # OUTPUT_DIR env var is injected by the runtime and points to the sandbox out/ dir
    lines = [
        "from pathlib import Path",
        "import os, time",
        "",
        "out_dir = Path(os.environ['OUTPUT_DIR'])",
        f"out_path = out_dir / '{turn_id}/files/hello-iso-runtime.txt'",
        "out_path.parent.mkdir(parents=True, exist_ok=True)",
    ]

    # Each branch appends scenario-specific code to the preamble
    if scenario.id == "0":  # Happy path: write file + call a tool
        lines += [
            "out_path.write_text('hello from iso-runtime', encoding='utf-8')",
            "await agent_io_tools.tool_call(",
            "    fn=local_tools.write_note,",
            "    params={'text': 'note from iso-runtime'},",
            "    call_reason='Write a simple note file',",
            "    tool_id='local_tools.write_note'",
            ")",
        ]
    elif scenario.id == "1":  # Timeout with no output at all
        lines += ["while True: pass"]
        timeout_s = 3
    elif scenario.id == "2":  # Timeout after some stdout output
        lines += ["print('progress before timeout')", "while True: pass"]
        timeout_s = 3
    elif scenario.id == "3":  # Clean crash via exception
        lines += ["raise RuntimeError('simulated program crash')"]
    elif scenario.id == "4":  # Partial success: only 1 of 2 contracted files produced
        contract = contract + [
            {
                "filename": f"{turn_id}/files/missing-output.txt",
                "description": "This file is intentionally missing.",
            }
        ]
        lines += ["out_path.write_text('partial output', encoding='utf-8')"]
    elif scenario.id == "5":  # MemoryError
        lines += ["raise MemoryError('simulated memory error')"]
    elif scenario.id == "6":  # Stack overflow via infinite recursion
        lines += [
            "def _recurse():",
            "    return _recurse()",
            "_recurse()",
        ]
    elif scenario.id == "7":  # Timeout via sleep (blocked on I/O)
        lines += ["print('sleeping...')", "time.sleep(999)"]
        timeout_s = 3
    elif scenario.id == "8":  # Timeout via busy loop (CPU-bound)
        lines += ["print('looping...')", "while True: pass"]
        timeout_s = 3
    elif scenario.id == "9":  # Stdout output followed by crash
        lines += [
            "print('hello from print before crash')",
            "raise RuntimeError('simulated crash after print')",
        ]
    elif scenario.id == "10":  # Successful write + logging at INFO and ERROR levels
        lines += [
            "import logging",
            "out_path.write_text('log-only success', encoding='utf-8')",
            "logger = logging.getLogger('user')",
            "logger.info('info from program logger')",
            "logger.error('error from program logger')",
        ]
    elif scenario.id == "11":  # File written successfully, then crash (partial artifact)
        lines += [
            "out_path.write_text('written before crash', encoding='utf-8')",
            "raise RuntimeError('simulated crash after write')",
        ]
    elif scenario.id == "12":  # Side-effects mode: no contract, just diff the output dir
        contract = []
        lines += [
            "out_path.write_text('side-effects output', encoding='utf-8')",
            "extra_path = out_dir / f'{turn_id}/files/extra-side-effect.txt'",
            "extra_path.parent.mkdir(parents=True, exist_ok=True)",
            "extra_path.write_text('another output', encoding='utf-8')",
        ]
    elif scenario.id == "13":  # Fargate path: remote runtime selected via bundle props
        # Fargate cold start + image pull + secret injection routinely takes
        # much longer than the local/docker demo paths.
        timeout_s = 180
        lines += [
            "out_path.write_text('hello from fargate iso-runtime', encoding='utf-8')",
            "print('fargate scenario wrote expected output')",
        ]
    return {
        "contract": contract,
        "timeout_s": timeout_s,
        "code": "\n".join(lines),               # Join all lines into a single Python script
        "use_contract": scenario.id != "12",     # Scenario 12 uses side-effects mode
    }
