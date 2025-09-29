from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore


async def get_pg_pool(_settings):
    global _pg_pool

    import asyncpg, json
    async def _init_conn(conn: asyncpg.Connection):
        # Encode/decode json & jsonb as Python dicts automatically
        await conn.set_type_codec('json',  encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
        await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')

    _pg_pool = await asyncpg.create_pool(
        host=_settings.PGHOST,
        port=_settings.PGPORT,
        user=_settings.PGUSER,
        password=_settings.PGPASSWORD,
        database=_settings.PGDATABASE,
        ssl=_settings.PGSSL,
        init=_init_conn,
    )
    return _pg_pool

async def main():
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    _settings = get_settings()

    pg_pool = await get_pg_pool(_settings)
    conv_idx = ConvIndex(pool=pg_pool)
    conv_store = ConversationStore(_settings.STORAGE_PATH)
    ctx_client = ContextRAGClient(conv_idx=conv_idx,
                                  store=conv_store,
                                  model_service=None)
    await conv_idx.init()

    user_id = "admin-user-1"
    conversation_id = "7c41b8e3-27cd-48ff-840e-9e158d1ee193"
    conversation_id = "a"

    conversation_id = "01c414d5-ef92-402a-ae1b-77d493961329"
    turns = await conv_idx.get_conversation_turn_ids_from_tags(user_id=user_id, conversation_id=conversation_id)
    print(f"Turns: {turns}")

    conversations = await ctx_client.list_conversations(user_id=user_id, last_n=2)

    is_new_conversation = len(turns) == 0
    c_details = await ctx_client.get_conversation_details(user_id=user_id, conversation_id=conversation_id)
    print()
    print(f"Conversations: {conversations}\nIs new: {is_new_conversation}\nDetails: {c_details}")

    conversation_artifacts = await ctx_client.fetch_conversation_artifacts(user_id=user_id,
                                                                           conversation_id=conversation_id,
                                                                           materialize=True)
    conversation_id = None
    FINGERPRINT_KIND = "artifact:turn.fingerprint.v1"
    CONV_START_FPS_TAG = "conv.start"
    data = await ctx_client.search(kinds=[FINGERPRINT_KIND],
                                   user_id=user_id,
                                   conversation_id=conversation_id,
                                   all_tags=[CONV_START_FPS_TAG],
                                   )

    conv_start = next(iter(data), None) if data else None
    conversation_title = conv_start.get("conversation_title") if conv_start else None
    print(f"Conv start: {conv_start}\nConversation title: '{conversation_title}'")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())