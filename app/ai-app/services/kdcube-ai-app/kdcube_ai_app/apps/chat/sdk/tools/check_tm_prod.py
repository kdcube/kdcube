from dataclasses import asdict

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator


# chat/sdk/tools/check_tm_prod.py
import asyncio, json, pathlib, textwrap, os
from typing import Dict, Any

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.codegen.team import _today_str
# --- import your classes ---
from kdcube_ai_app.apps.chat.sdk.codegen.codegen_tool_manager import ToolManager, SolutionPlannerDecision
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger, ModelServiceBase, ConfigRequest, create_workflow_config

# --- Minimal communicator that logs 'thinking' deltas to stdout ---
class LogCommunicator:
    async def delta(self, *, text: str, index: int, marker: str, agent: str, completed: bool = False):
        print(f"[{agent}:{index} {marker}] {text}")

# --- Simple emit callback that just logs structured events ---
async def emit_event(evt: Dict[str, Any], rid: str):
    print(f"\n=== EVENT {rid} :: {evt['title']} ===")
    print(json.dumps(evt, indent=2, ensure_ascii=False))

# Dummy service (not used by stub, but ToolManager wants something)
class DummyService(ModelServiceBase):
    pass

async def experiment():

    comm = LogCommunicator()

    settings = get_settings()
    tenant = os.getenv("TENANT_ID")
    project = os.getenv("DEFAULT_PROJECT_NAME")
    user = "user123"
    session_id = "sess456"
    conversation_id = "conv456"

    topics = ["python", "news"]

    cfg_req = ConfigRequest(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        claude_api_key=os.getenv("ANTHROPIC_API_KEY"),
        selected_model="claude-3-7-sonnet-20250219", # default model
        role_models= {
            "tool_router":   { "provider": "anthropic", "model": "claude-3-haiku-20240307" },
            "solvability":   { "provider": "anthropic", "model": "claude-3-haiku-20240307" },
            "solver_codegen": { "provider": "anthropic", "model": "claude-3-7-sonnet-20250219" },
            "tool.summarizer": {"provider": "anthropic", "model": "claude-3-7-sonnet-20250219" }
        }
    )
    cfg = create_workflow_config(cfg_req)
    svc = ModelServiceBase(config=cfg)

    logger = AgentLogger("debug")
    comm = LogCommunicator()
    # svc = DummyService()

    # Use a RELATIVE path to your tools module:
    gen_tools_rel = "./generic_agent_tools.py"  # adjust relative path as needed
    llm_tools_rel = "./llm_tools.py"  # adjust relative path as needed
    io_tools_rel = "./io_tools.py"  # adjust relative path as needed
    tm = ToolManager(
        service=svc,
        comm=comm,
        logger=logger,
        emit=emit_event,
        tools_modules=[
            {"ref": gen_tools_rel, "use_sk": True, "alias": "generic_tools"},
            {"ref": llm_tools_rel, "use_sk": True, "alias": "llm_tools"},
            {"ref": io_tools_rel, "use_sk": True, "alias": "io_tools"},
            # You can add more modules:
            # {"ref": "./more_tools.py", "use_sk": False, "alias": "more"},
        ], # relative path works with the loader patch
    )

    allowed_plugins = ["domain_specific_tools", "llm_tools", "generic_tools"]

    # 1) See inferred catalog & adapters
    print("\n--- CATALOG ---")
    print(json.dumps(tm.tool_catalog_for_prompt(allowed_plugins=allowed_plugins), indent=2, ensure_ascii=False))

    print("\n--- ADAPTERS ---")
    adapters = tm.adapters_for_codegen(allowed_plugins=allowed_plugins)
    print(json.dumps(adapters, indent=2, ensure_ascii=False))

    user_text = "What happened with Python this week? Summarize briefly in PDF on page one and on page 2 write the web sources where that was found, links and headings."
    # 2) (Optional) Run router+solvability using the stubbed streaming
    rid = "req-multi-003"

    policy_summary = ""
    ctx = {
        "request_id": rid,
        "text": user_text,
        "topics": topics,
        "policy_summary": policy_summary,
        "context_hint": "",
        "topic_hint": "",
        "is_spec_domain": True,
    }

    solution = await tm.solve(request_id=rid,
                              user_text=user_text,
                              policy_summary=policy_summary,
                              topics=topics,
                              section_name="experimental",
                              allowed_plugins=allowed_plugins)
    print()
    async def debug():
        decision_res = await tm.decide(ctx=ctx)
        print("\n--- Decision ---")
        print(json.dumps({
            "allow_kb": decision_res["allow_kb"],
            "clarifying_questions": decision_res["clarifying_questions"],
            "tools": [t.id for t in decision_res["decision"].tools],
        }, indent=2, ensure_ascii=False))

        sv = decision_res.get("sv") or {}
        decision: SolutionPlannerDecision = tm._materialize_decision(decision_res["tr"], decision_res["sv"])
        chosen = [t.id for t in (decision.tools or [])]

        # extra_task_hint = "Get current time and one search hit about Python."
        extra_task_hint = ""
        # Build task spec for the codegen agent
        task_spec = {
            "objective": user_text,
            "policy_summary": policy_summary[:1200],
            # "tools_selected": [asdict(t) for t in decision.tools],
            "notes": (extra_task_hint or {}),
        }

        async def code_exec():
            # 3) Prepare INPUTS and run a minimal solver snippet that uses your tools
            out_dir = pathlib.Path("./.tmp_solver") / rid
            scratch = {"user": {"prefs": {"style": "brief"}}, "turn_shared": {"topic": "python news"}}
            task = {"objective": user_text, "depends_on": []}
            tm.write_runtime_inputs(output_dir=out_dir, context=scratch, task=task)

            # Minimal snippet: reads OUTPUT_DIR, calls tools, writes result.json
            code = textwrap.dedent("""
                import os, json
                from agent_tools import tools  # module name = file stem loaded by ToolManager
        
                def read_json(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            return json.load(f)
                    except Exception:
                        return {}
        
                def main():
                    outdir = os.environ["OUTPUT_DIR"]
                    ctx = read_json(os.path.join(outdir, "context.json"))
                    task = read_json(os.path.join(outdir, "task.json"))
        
                    now_str = tools.now()
                    q = (task.get("objective") or "python")[:120]
                    hits_raw = tools.web_search(query=q, k=1)
                    try:
                        hits = json.loads(hits_raw)
                    except Exception:
                        hits = [{"raw": hits_raw}]
        
                    result = {"now": now_str, "query": q, "hits": hits}
                    with open(os.path.join(outdir, "result.json"), "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
        
                if __name__ == "__main__":
                    main()
            """).strip()

            run_res = await tm.run_solver_snippet(code=code, output_dir=out_dir, timeout_s=60)
            print("\n--- RUNTIME ---")
            print(json.dumps(run_res, indent=2, ensure_ascii=False))

            collected = tm.collect_outputs(
                output_dir=out_dir,
                outputs=[{"filename": "result.json", "kind": "json", "key": "worker.result"}],
            )
            print("\n--- OUTPUTS ---")
            print(json.dumps(collected, indent=2, ensure_ascii=False))

        async def auto_exec():

            section_name = "AUTO-EXEC"

            solver_mode = (sv.get("solver_mode") or ("single_call" if decision.tools and len(decision.tools) == 1 else "llm_only"))
            clarifying = sv.get("clarifying_questions") or []

            result: Dict[str, Any] = {
                "mode": solver_mode,
                "decision": {
                    "tools": [vars(t) for t in decision.tools],
                    "confidence": decision.confidence,
                    "reasoning": decision.reasoning,
                    "clarifying_questions": clarifying,
                },
                "artifacts": [],
            }
            # 4) CODEGEN path (adapters limited to chosen tools)
            chosen_ids = [t.id for t in decision.tools]
            adapters = tm.adapters_for_codegen(allowed_ids=chosen_ids)

            import tempfile
            from kdcube_ai_app.apps.chat.sdk.codegen.team import solver_codegen_stream  # local import to avoid cycles

            cg_stream = await solver_codegen_stream(
                tm.svc,
                task=task_spec,
                adapters=adapters,
                solvability=sv,
                must_persist_raw_of=["generic_tools.web_search"],
                on_thinking_delta=tm._mk_thinking_streamer("solver_codegen"),
                ctx="solver_codegen"
            )
            cg = (cg_stream or {}).get("agent_response") or {}
            files = cg.get("files") or []
            entrypoint = cg.get("entrypoint") or "python main.py"
            outputs = cg.get("outputs") or [{"filename": "result.json", "kind": "json", "key": "solver_output"}]

            # materialize → run → collect
            # tmp = pathlib.Path(tempfile.mkdtemp(prefix="solver_"))
            tmp = pathlib.Path("./.tmp_solver/req-multi-003")
            workdir = tmp / "pkg"; outdir = tmp / "out"
            workdir.mkdir(parents=True, exist_ok=True); outdir.mkdir(parents=True, exist_ok=True)

            # write context + task for the generated program
            task_spec["adapters_spec"] = adapters  # pass adapters for info
            tm.write_runtime_inputs(
                output_dir=outdir,
                context={"request_id": rid, "topics": topics, "policy_summary": policy_summary, "today": _today_str()},
                task=task_spec
            )

            files_map = {f["path"]: f["content"] for f in files if f.get("path") and f.get("content") is not None}
            run_res = await tm.run_main_py_package(workdir=workdir, output_dir=outdir, files=files_map, timeout_s=120)
            collected = tm.collect_outputs(output_dir=outdir, outputs=outputs)

            result["codegen"] = {
                "entrypoint": entrypoint,
                "files": [{"path": p, "size": len(c or "")} for p, c in files_map.items()],
                "run": run_res,
                "outputs": collected,
                "notes": cg.get("notes", ""),
            }

            # artifacts (code preview + outputs)
            # only inline main.py (short); others we summarize
            main_src = files_map.get("main.py")
            if main_src:
                result["artifacts"].append(tm._artifact(
                    "solver-code",
                    f"[{section_name}] Solver main.py",
                    (main_src if len(main_src) <= 8000 else (main_src[:8000] + "\n...[truncated]"))
                ))
            result["artifacts"].append(tm._artifact(
                "solver-outputs",
                f"[{section_name}] Solver Outputs",
                json.dumps(collected, ensure_ascii=False, indent=2)
            ))
            result["artifacts"].append(tm._artifact(
                "tool-decision",
                f"[{section_name}] Tool Decision",
                json.dumps({"solver_mode": solver_mode, **result["decision"]}, ensure_ascii=False, indent=2)
            ))
            print()

    # await code_exec()
    # await debug()

async def main():

    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    await experiment()

if __name__ == "__main__":
    asyncio.run(main())
