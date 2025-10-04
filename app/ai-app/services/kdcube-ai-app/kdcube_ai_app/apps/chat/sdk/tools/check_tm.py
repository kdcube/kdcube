

# debug_harness.py
import asyncio, json, pathlib, textwrap, os
from typing import Dict, Any

from kdcube_ai_app.apps.chat.sdk.config import get_settings
# --- import your classes ---
from kdcube_ai_app.apps.chat.sdk.codegen.codegen_tool_manager import ToolManager
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger, ModelServiceBase, ConfigRequest, create_workflow_config
# We'll monkeypatch _stream_agent_sections_to_json to stub router/solvability (optional)
from kdcube_ai_app.apps.chat.sdk.tools import team as team_mod

# --- Minimal communicator that logs 'thinking' deltas to stdout ---
class LogCommunicator:
    async def delta(self, *, text: str, index: int, marker: str, agent: str, completed: bool = False):
        print(f"[{agent}:{index} {marker}] {text}")

# --- Simple emit callback that just logs structured events ---
async def emit_event(evt: Dict[str, Any], rid: str):
    print(f"\n=== EVENT {rid} :: {evt['title']} ===")
    print(json.dumps(evt, indent=2, ensure_ascii=False))

# Stub model streaming so decide() works
async def _stub_stream_agent_sections_to_json(*args, **kwargs):
    client = kwargs.get("client_name")
    on_thinking = kwargs.get("on_thinking_delta")
    if on_thinking: await on_thinking("planning")
    if client == "tool_router":
        # choose two tools by their QUALIFIED ids (alias.fn)
        return {"agent_response": {
            "candidates": [
                {"name": "agent_tools.web_search", "reason": "fetch facts", "confidence": 0.7, "parameters": {"k": 2}},
                {"name": "agent_tools.now", "reason": "time stamp", "confidence": 0.6, "parameters": {}},
            ],
            "notes": "stubbed",
        }}
    if client == "solvability":
        return {"agent_response": {
            "solvable": True, "confidence": 0.8, "reasoning": "ok", "tools_to_use": ["agent_tools.web_search","agent_tools.now"], "clarifying_questions": []
        }}
    if client == "solver_codegen":
        return {"agent_response": {}}
    return {"agent_response": {}}

team_mod._stream_agent_sections_to_json = _stub_stream_agent_sections_to_json  # comment out to use real models

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

    cfg_req = ConfigRequest(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        claude_api_key=os.getenv("ANTHROPIC_API_KEY"),
        selected_model="claude-3-7-sonnet-20250219", # default model
        role_models= {
            "tool_router":   {"provider": "anthropic",    "model": "claude-3-haiku-20240307"},
            "solvability":   {"provider": "anthropic", "model": "claude-3-haiku-20240307"},
            "preference_extractor": {"provider": "anthropic", "model": "claude-3-haiku-20240307"},
        }
    )
    cfg = create_workflow_config(cfg_req)
    svc = ModelServiceBase(config=cfg)

    logger = AgentLogger("debug")
    comm = LogCommunicator()
    # svc = DummyService()

    # Use a RELATIVE path to your tools module:
    tools_rel = "./test_tools.py"  # adjust relative path as needed
    tm = ToolManager(
        service=svc,
        comm=comm,
        logger=logger,
        emit=emit_event,
        tools_modules=[
            {"ref": "./agent_tools.py", "use_sk": True, "alias": "agent_tools"},
            # You can add more modules:
            # {"ref": "./more_tools.py", "use_sk": False, "alias": "more"},
        ], # relative path works with the loader patch
    )

    # 1) See inferred catalog & adapters
    print("\n--- CATALOG ---")
    print(json.dumps(tm.tool_catalog_for_prompt(), indent=2, ensure_ascii=False))

    print("\n--- ADAPTERS ---")
    adapters = tm.adapters_for_codegen()
    print(json.dumps(adapters, indent=2, ensure_ascii=False))

    # 2) (Optional) Run router+solvability using the stubbed streaming
    rid = "req-multi-001"
    ctx = {
        "request_id": rid,
        "text": "What happened with Python this week? Summarize briefly.",
        "topics": ["python", "news"],
        "policy_summary": "",
        "context_hint": "",
        "topic_hint": "",
        "is_demo_domain": True,
    }
    decision_res = await tm.decide(ctx=ctx)
    tr = decision_res.get("tr") or {}
    sv = decision_res.get("sv") or {}

    async def decision_only():

        print("\n--- Decision ---")
        print(json.dumps({
            "allow_kb": decision_res["allow_kb"],
            "clarifying_questions": decision_res["clarifying_questions"],
            "tools": [t.id for t in decision_res["decision"].tools],
        }, indent=2, ensure_ascii=False))

        # 3) Prepare INPUTS and run a minimal solver snippet that uses your tools
        out_dir = pathlib.Path("./.tmp_solver") / rid
        scratch = {"user": {"prefs": {"style": "brief"}}, "turn_shared": {"topic": "python news"}}
        task = {"objective": "Get current time and one search hit about Python.", "depends_on": []}
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


    await decision_only()

async def main():
    await experiment()
if __name__ == "__main__":
    asyncio.run(main())
