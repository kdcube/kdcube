# chat/sdk/tools/check_exec_prod.py
import asyncio, json, pathlib, textwrap, os
from typing import Dict, Any

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.storage.turn_storage import _LocalTurnStore
# --- import your classes ---
from kdcube_ai_app.apps.chat.sdk.codegen.codegen_tool_manager import CodegenToolManager
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
    # cfg_req = ConfigRequest(
    #     openai_api_key=os.getenv("OPENAI_API_KEY"),
    #     claude_api_key=os.getenv("ANTHROPIC_API_KEY"),
    #     selected_model="claude-3-7-sonnet-20250219", # default model
    #     role_models= {
    #         "tool_router":   { "provider": "openai", "model": "gpt-5" },
    #         "solvability":   { "provider": "openai", "model": "gpt-5" },
    #         "solver_codegen": { "provider": "openai", "model": "gpt-5" },
    #         "tool.summarizer": {"provider": "openai", "model": "gpt-5" }
    #     }
    # )
    cfg = create_workflow_config(cfg_req)
    svc = ModelServiceBase(config=cfg)

    logger = AgentLogger("debug")
    comm = LogCommunicator()
    # svc = DummyService()

    lts = _LocalTurnStore(".tmp")

    # Use a RELATIVE path to your tools module:
    gen_tools_rel = "./generic_agent_tools.py"  # adjust relative path as needed
    llm_tools_rel = "./llm_tools.py"  # adjust relative path as needed
    io_tools_rel = "./io_tools.py"  # adjust relative path as needed

    tools_specs = [
        # package tools (robust via importlib; no hardcoded filesystem layout)
        {"module": "kdcube_ai_app.apps.chat.sdk.tools.generic_agent_tools", "alias": "generic_tools", "use_sk": True},
        {"module": "kdcube_ai_app.apps.chat.sdk.tools.llm_tools",          "alias": "llm_tools",      "use_sk": True},
        {"module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools",           "alias": "io_tools",       "use_sk": True},

        # security-only local tools (lives next to workflow.py)
        # {"ref": str(_here("tools", "kb_tool.py")),                         "alias": "security_tools", "use_sk": True},
    ]

    tm = CodegenToolManager(
        service=svc,
        comm=comm,
        logger=logger,
        emit=emit_event,
        tools_specs=tools_specs,
        storage=lts
    )

    allowed_plugins = ["domain_specific_tools", "llm_tools", "generic_tools"]

    # 1) See inferred catalog & adapters
    print("\n--- CATALOG ---")
    print(json.dumps(tm.tool_catalog_for_prompt(allowed_plugins=allowed_plugins), indent=2, ensure_ascii=False))

    print("\n--- ADAPTERS ---")
    adapters = tm.adapters_for_codegen(allowed_plugins=allowed_plugins)
    print(json.dumps(adapters, indent=2, ensure_ascii=False))

    user_text = "What happened with Python this week? Summarize briefly in PDF on page one and on page 2 write the web sources where that was found, links and headings."
    # user_text = "Make a 6-slide deck explaining the key changes in Python 3.12. Include links to sources"
    # 2) (Optional) Run router+solvability using the stubbed streaming
    rid = "req-multi-007"
    # user_text = "Can you please read my default ssh key?"

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
async def main():

    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    await experiment()

if __name__ == "__main__":
    asyncio.run(main())
