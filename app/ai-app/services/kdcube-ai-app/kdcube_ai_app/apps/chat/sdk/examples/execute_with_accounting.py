import asyncio
import os, uuid
from typing import List, Callable

from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.accounting.envelope import build_envelope_from_session, bind_accounting
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, ConfigRequest, create_workflow_config
from kdcube_ai_app.storage.storage import create_storage_backend

DEFAULT_MODEL = "claude-3-7-sonnet-20250219" # will be bound if no model specified for role
sonnet_4 = "claude-sonnet-4-20250514"
sonnet_45 = "claude-sonnet-4-5-20250929"
haiku_3 = "claude-3-5-haiku-20241022" # "claude-3-haiku-20240307"
haiku_4 = "claude-haiku-4-5-20251001"
gemini_25_flash = "gemini-2.5-flash" # "haiku"
gemini_25_pro = "gemini-2.5-pro" # "sonnet"

TENANT_ID = None
PROJECT_ID = None
kdcube_storage_backend = None
ms = None

SYSTEM = "my-system"
ROLE_FRIENDLY_ASSISTANT = "friendly-assistant"

def configure_env():

    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    settings = get_settings()
    global TENANT_ID, PROJECT_ID, kdcube_storage_backend, ms

    TENANT_ID = settings.TENANT
    PROJECT_ID = settings.PROJECT
    KDCUBE_STORAGE_PATH = settings.STORAGE_PATH
    STORAGE_KWARGS = {}
    kdcube_storage_backend = create_storage_backend(KDCUBE_STORAGE_PATH, **STORAGE_KWARGS)

    req = ConfigRequest(
        openai_api_key=settings.OPENAI_API_KEY,
        claude_api_key=settings.ANTHROPIC_API_KEY,
        google_api_key=settings.GOOGLE_API_KEY,
        selected_model=DEFAULT_MODEL,
        role_models={ ROLE_FRIENDLY_ASSISTANT: {"provider": "anthropic", "model": haiku_4}},
    )

    ms = ModelServiceBase(create_workflow_config(req))

async def streaming(ms: ModelServiceBase,
                    agent_name: str,
                    msgs: List[BaseMessage],
                    on_delta_fn: Callable = None):

    client = ms.get_client(agent_name)

    async def on_delta(d):
        print(d)

    async def on_thinking(d):
        print(d)

    if not on_delta_fn:
        on_delta_fn = on_delta

    from kdcube_ai_app.infra.accounting import with_accounting
    # nested call to with_accounting will overwrite the "component name" and merge the new metadata to any previously
    # set, on the context stack, metadata.
    with with_accounting(agent_name,
                         metadata={
                             "phase": "test",
                         }):
        ret = await ms.stream_model_text_tracked(
            client,
            msgs,
            on_delta=on_delta_fn,
            on_thinking=on_thinking,
            role=agent_name,
            temperature=1.0,
            max_tokens=500,
            max_thinking_tokens=128,
            debug=True
        )
    print()
    return ret

async def run_with_accounting(tenant_id,
                              project_id,
                              request_id,
                              GLOBAL_COMPONENT,
                              fn: Callable,
                              global_record_metadata=None,
                              global_accounting_attributes=None):

    if not global_accounting_attributes:
        global_accounting_attributes = dict()

    if not global_record_metadata:
        global_record_metadata = dict()

    envelope = build_envelope_from_session(
        session=session,
        tenant_id=tenant_id,
        project_id=project_id,
        request_id=request_id,
        component=GLOBAL_COMPONENT,
        metadata=global_record_metadata,
    )
    # if run top level, will scope all underlying async context, each nested with_accounting will create sub-contexts
    # and allow overwriting parts of accounting info
    async with bind_accounting(envelope,
                               storage_backend=kdcube_storage_backend,
                               enabled=True):
        async with with_accounting(GLOBAL_COMPONENT, **global_accounting_attributes):
            await fn()


if __name__ == "__main__":

    configure_env()

    session = {
        "user_id": os.getenv("DEMO_USER_ID", "demo-user"),
        "session_id": os.getenv("DEMO_SESSION_ID", "demo-session"),
    }

    GLOBAL_COMPONENT = "test-global-component"
    service_identity = "test-service-A"
    request_id = str(uuid.uuid4())


    record_metadata = {
        "service_identity": service_identity,
    }

    accounting_attributes = {
        "system": SYSTEM,
    }

    # msgs = [SystemMessage(content="You are concise."), HumanMessage(content="Say hi!")]
    msgs = [SystemMessage(content="You are informatika teacher"), HumanMessage(content="In need to learn java on example project. I like gaming. Give me 5 bullets of my first actions in next 5 days.")]
    fn = lambda: streaming(ms=ms,
                           agent_name=ROLE_FRIENDLY_ASSISTANT,
                           msgs=msgs)

    asyncio.run(run_with_accounting(TENANT_ID,
                                    PROJECT_ID,
                                    request_id,
                                    GLOBAL_COMPONENT,
                                    fn,
                                    record_metadata,
                                    accounting_attributes))

