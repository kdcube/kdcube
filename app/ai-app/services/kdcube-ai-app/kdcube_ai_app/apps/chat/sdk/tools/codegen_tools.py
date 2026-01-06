# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/codegen/codegen_tool.py
from __future__ import annotations

import semantic_kernel as sk

import json
import uuid
import pathlib
from typing import Any, Dict, List, Optional, Annotated, Callable, Awaitable

from kdcube_ai_app.apps.chat.sdk.runtime.solution.react.context import ReactContext
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

class CodegenTool:
    @kernel_function(
        name="codegen_python",
        description=(
                "Generate + execute a single Python 3.11 program that produces EXACTLY the artifacts described by `output_contract`.\n"
                "\n"
                "WHEN TO USE\n"
                "- Use this tool when the step requires producing one or more concrete deliverables (inline text and/or files)\n"
                "  and the work may require multiple tool calls, transformations, or rendering.\n"
                "  This includes the cases when the work must be done using the existing artifacts in context (improve, fix, continue, tarnsform, synthetize, etc.).\n"
                " Important note on context this tool will receive: this tool is always provided with the same context as you see now. \n"
                " This allows you to refer, in the instruction, to named context artifacts that you see by naming the turn and artifacts. \n"
                " This allows you to ask codegen to use the context objects as a base for future work according to your instruction and advice. \n"
                " Another case is when the data must be retrieved in a pipeline way and stitched between the visible integrations.\n"
                " Another case is when the binary data must be synthetized, or for some simulations, or for data analyzis, building the reactive code etc..\n"
                " One of the popular cases is to create excel file with data as must come from some research. Another is when there are images (or they should be generated in-round) and "
                "some texts, and the pdf must be synthetized. \n"
                "- Do NOT use it for simple single-tool lookups.\n"
                
                "\n"
                "INPUTS (YOU MUST PROVIDE BOTH)\n"
                "1) `instruction` (string): must include the effective objective for THIS step.\n"
                "   - Must describe what the artifacts are, what they should contain, and any constraints (tone/length/format).\n"
                "   - Must be consistent with `output_contract`.\n"
                "   - MUST NOT request extra deliverables beyond what is listed in `output_contract`.\n"
                "   - May contain your advices and how to solve the objective, what to avoid, some facts you know shortly. Telegraphic style\n"
                "\n"
                "2) `output_contract` (JSON object or dict): map of artifact_id -> artifact spec.\n"
                "   - Keys (artifact_id) become the artifact names in the result.\n"
                "   - You must list ALL artifacts required for this step.\n"
                "   - The executor will produce EXACTLY these artifacts (no more).\n"
                "\n"
                "OUTPUT_CONTRACT SCHEMA\n"
                "output_contract := {\n"
                "  \"<artifact_id>\": {\n"
                "     \"type\": \"inline\" | \"file\",\n"
                "     \"description\": \"human readable description\",\n"
                "     \"content_guidance\": \"(optional) short constraints: length/sections/tone\", \n"
                "\n"
                "     # If type == \"inline\":\n"
                "     \"format\": \"markdown\" | \"json\" | \"yaml\" | \"html\" | \"text\",\n"
                "\n"
                "     # If type == \"file\":\n"
                "     \"filename\": \"relative/path/in/OUTPUT_DIR.ext\",\n"
                "     \"mime\": \"application/pdf\" | \"text/csv\" | \"application/vnd.openxmlformats-officedocument...\" | ...\n"
                "  }\n"
                "}\n"
                "\n"
                "RULES (STRICT)\n"
                "- The tool will produce EXACTLY the artifacts listed in the contract.\n"
                "- For inline artifacts you MUST specify `format`.\n"
                "- For file artifacts you MUST specify both `filename` and `mime`.\n"
                "- If you need sources/citations, request them inside `content_guidance`.\n"
                "- Tone, structural/semantic expectations - specify in `content_guidance`. Teelgraphic style\n"
                "- Keep artifact_ids stable and machine-safe (snake_case recommended).\n"
                "\n"
                "RETURN VALUE\n"
                "- The system returns a result with the produced artifacts keyed by the SAME artifact_ids\n"
                "  as in output_contract (no aliases). Downstream consumes these items to map slots.\n"
                "  Practical shape:\n"
                "  {\n"
                "    \"ok\": true|false,\n"
                "    \"error\": { ... } | null,\n"
                "    \"items\": [\n"
                "      {\"artifact_id\": \"<contract_id>\", \"type\": \"inline|file\", \"format\"?: \"...\", \"mime\"?: \"...\",\n"
                "       \"description\"?: \"...\", \"sources_used\"?: [...], \"draft\"?: true, \"summary\"?: \"...\"}\n"
                "    ]\n"
                "  }\n"
                "\n"
                "ARTIFACT PAYLOAD SHAPES\n"
                "- inline: {\"type\":\"inline\",\"format\":\"markdown|json|...\",\"value\":...,\"description\":...,\"sources_used\"?:[...] ,\"draft\"?:true}\n"
                "- file:   {\"type\":\"file\",\"path\":\"<relative>\",\"mime\":\"...\",\"text\":\"<surrogate markdown>\",\"description\":...,\"sources_used\"?:[...] ,\"draft\"?:true}\n"
                "\n"
                "EXAMPLE\n"
                "{\n"
                "  \"reasoning\": \"Create an executive summary and a PDF brief with citations.\",\n"
                "  \"output_contract\": {\n"
                "    \"summary_md\": {\n"
                "      \"type\": \"inline\",\n"
                "      \"format\": \"markdown\",\n"
                "      \"description\": \"1-page executive summary\",\n"
                "      \"content_guidance\": \"~400 words, 3 bullet sections\"\n"
                "    },\n"
                "    \"brief_pdf\": {\n"
                "      \"type\": \"file\",\n"
                "      \"filename\": \"brief.pdf\",\n"
                "      \"mime\": \"application/pdf\",\n"
                "      \"description\": \"2–3 page PDF brief\",\n"
                "      \"content_guidance\": \"include citations; formal, concise(Boardsitzung);avoid fuzziness;concrete numbers + plots\"\n"
                "    }\n"
                "  }\n"
                "}\n"
        )
    )
    async def codegen_python(
            self,
            output_contract: Annotated[dict | str, (
                    "JSON object (or JSON string) mapping artifact_id -> spec. "
                    "Must follow the schema in the tool description. "
                    "Do not include extra keys beyond the artifacts you want produced."
            )],
            instruction: Annotated[str, (
                    "Effective objective for THIS step. "
                    "Must be consistent with output_contract and must not request extra deliverables."
            )],
    ) -> Annotated[dict, "Result: {ok, error?, items:[{artifact_id, type, format?, mime?, description?, sources_used?, draft?, summary?}]}."]:
        pass

async def run_codegen_tool(
        *,
        codegen: "CodegenRunner",
        output_contract: Dict[str, Any],
        instruction: str,
        reasoning: str,
        allowed_plugins: List[str],
        context: ReactContext,
        solution_gen_stream: Callable[..., Awaitable[Dict[str, Any]]],
        logger: Optional[AgentLogger] = None,
        outdir: Optional[pathlib.Path] = None,
        workdir: Optional[pathlib.Path] = None,
        exec_id: Optional[str] = None,
        invocation_idx: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Supervisor-side meta-tool wrapper.

    Returns an envelope suitable for React exec:
      {
        "ok": bool,
        "reasoning": str,
        "result_filename": str,
        "run_id": str,
        "workdir": str,
        "outdir": str,
        "out_dyn": dict,
        "out": list,            # normalized artifacts list (slots + promoted tool calls)
        "sources_pool": list,   # optional
        "error": dict|None,
        "summary": str
      }
    """
    log = logger or AgentLogger("codegen.tool")

    # 1) unique per invocation
    result_filename = f"codegen_result_{exec_id}.json" if exec_id else f"codegen_result_{uuid.uuid4().hex[:10]}.json"

    # 2) run codegen in docker (via CodegenRunner.run_as_a_tool)
    log.log(f"[codegen.tool] starting: reasoning={reasoning}; instruction={instruction}", level="INFO")
    run_rec = await codegen.run_as_a_tool(
        program_playbook=context.operational_digest,
        output_contract=output_contract,
        allowed_plugins=allowed_plugins,
        result_filename=result_filename,
        bundle=context.context_bundle,
        instruction=instruction,
        outdir=outdir,
        workdir=workdir,
        solution_gen_stream=solution_gen_stream,
        exec_id=exec_id,
        invocation_idx=invocation_idx,
    )
    outdir = pathlib.Path(run_rec.get("outdir") or "")
    workdir = pathlib.Path(run_rec.get("workdir") or "")
    run_id = run_rec.get("run_id") or ""

    # 3) read the produced result json (written by save_ret in iso runtime header)
    result_path = outdir / result_filename
    if not result_path.exists():
        # hard error: codegen run didn’t produce a result file
        err = {
            "where": "codegen.tool",
            "error": "missing_result_file",
            "description": f"Expected result file not found: {result_filename}",
            "managed": True,
            "details": {"outdir": str(outdir), "workdir": str(workdir), "run_id": run_id},
        }
        return {
            "ok": False,
            "instruction": instruction,
            "result_filename": result_filename,
            "run_id": run_id,
            "workdir": str(workdir),
            "outdir": str(outdir),
            "artifacts": [],
            "sources_pool": [],
            "error": err,
            "summary": "ERROR: missing result file",
        }

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        err = {
            "where": "codegen.tool",
            "error": "malformed_result_json",
            "description": str(e),
            "managed": True,
            "details": {"path": str(result_path)},
        }
        return {
            "ok": False,
            "reasoning": reasoning,
            "result_filename": result_filename,
            "run_id": run_id,
            "workdir": str(workdir),
            "outdir": str(outdir),
            "artifacts": [],
            "sources_pool": [],
            "error": err,
            "summary": "ERROR: malformed result.json",
        }

    ok = bool(payload.get("ok", False))
    out = payload.get("out") or []
    out_dyn = payload.get("out_dyn") or {}
    artifact_lvl = "artifact"
    project_log = None
    if isinstance(out_dyn, dict) and out_dyn:
        artifacts = []
        for name, artifact in out_dyn.items():
            if not isinstance(artifact, dict):
                continue
            if name == "project_log":
                project_log = artifact
                continue
            artifacts.append(
                {
                    "resource_id": f"{artifact_lvl}:{name}",
                    "output": artifact,
                    "type": artifact.get("type"),
                    "mime": artifact.get("mime"),
                    "format": artifact.get("format"),
                    "description": artifact.get("description"),
                    "sources_used": artifact.get("sources_used"),
                    "draft": artifact.get("draft"),
                    "content_inventorization": artifact.get("content_inventorization"),
                }
            )
    else:
        # Artifacts are emitted as resource_id="{artifact_lvl}:<name>" in the result envelope.
        artifacts = [
            item for item in out
            if (item.get("resource_id") or item.get("name") or "").startswith(f"{artifact_lvl}:")
            and not (item.get("resource_id") or item.get("name") or "").endswith(":project_log")
        ]
    sources_pool = payload.get("sources_pool") or []
    error = payload.get("error")
    if error:
        ok = False

    return {
        "ok": ok,
        "reasoning": reasoning,
        "result_filename": result_filename,
        "run_id": run_id,
        "workdir": str(workdir),
        "outdir": str(outdir),
        "artifacts": artifacts,
        "sources_pool": sources_pool,
        "error": error,
        "project_log": project_log,
    }

# module-level exports
kernel = sk.Kernel()
tools = CodegenTool()
kernel.add_plugin(tools, "codegen_tools")
