# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Simple Knowledge Base Debug Script
Minimal version for quick testing without LLM features
"""
import asyncio
import os
import time
from pathlib import Path
import logging

from kdcube_ai_app.apps.chat.reg import MODEL_CONFIGS, EMBEDDERS
from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.knowledge_base.db.providers.tenant_db import TenantDB
from kdcube_ai_app.apps.knowledge_base.tenant import TenantProjects
from kdcube_ai_app.infra.llm.llm_data_model import AIProvider, ModelRecord, AIProviderName

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KB.Debug pipeline")

async def metadata_model() -> ModelRecord:

    provider = AIProviderName.open_ai
    provider = AIProvider(provider=provider,
                          apiToken=await get_secret("services.openai.api_key", default="") or "")
    # model_config = MODEL_CONFIGS.get("gpt-4.1-nano", {})
    model_config = MODEL_CONFIGS.get("gpt-4o", {})
    model_name = model_config.get("model_name")

    model_record = ModelRecord(modelType="base",
                               status="active",
                               provider=provider,
                               systemName=model_name)

    return model_record

async def embedding_model() -> ModelRecord:

    # from kdcube_ai_app.infra.embedding.embedding import embedder_model
    # Use OpenAI embeddings (1536 dimensions)
    # return embedder_model(size=1536, get_key_fn=get_api_key)
    provider = AIProviderName.open_ai
    provider = AIProvider(provider=provider,
                          apiToken=await get_secret("services.openai.api_key", default="") or "")
    model_config = EMBEDDERS.get("openai-text-embedding-3-small")
    model_name = model_config.get("model_name")
    dim = model_config.get("dim")
    return ModelRecord(
        modelType="base",
        status="active",
        provider=provider,
        systemName=model_name,
        metadata={
            "dim": dim
        }
    )

def _embedding_model_without_provider_secret() -> ModelRecord:
    provider = AIProviderName.open_ai
    provider_record = AIProvider(provider=provider, apiToken=os.getenv("OPENAI_API_KEY") or "")
    model_config = EMBEDDERS.get("openai-text-embedding-3-small")
    model_name = model_config.get("model_name")
    dim = model_config.get("dim")
    return ModelRecord(
        modelType="base",
        status="active",
        provider=provider_record,
        systemName=model_name,
        metadata={"dim": dim},
    )

TENANT_ID = os.environ.get("TENANT_ID", "home")
ENABLE_DATABASE = os.environ.get("ENABLE_DATABASE", "true").lower() == "true"

# Create TenantDB singleton
_tenant_db = TenantDB(tenant_id=TENANT_ID) if ENABLE_DATABASE else None


def get_tenant():
    return TENANT_ID

def get_tenant_db() -> TenantDB:
    """Singleton TenantDB instance."""
    if not _tenant_db:
        raise RuntimeError("TenantDB not available (database support disabled)")
    return _tenant_db


async def simple_kb_debug(workdir: str,
                          pdf_filepath: str):
    """Simple debug function for basic KB pipeline testing"""

    print("=== SIMPLE KB DEBUG ===")

    # Basic imports
    from kdcube_ai_app.storage.storage import create_storage_backend
    from kdcube_ai_app.apps.knowledge_base.core import KnowledgeBase
    from kdcube_ai_app.tools.datasource import FileDataElement

    # Configuration
    import os
    project = os.environ.get("DEFAULT_PROJECT_NAME", None)
    tenant = os.environ.get("DEFAULT_TENANT", None)

    tenant_db = TenantDB(tenant)
    tenant_db.create_project_db(project_name=project, component_type="knowledge_base")

    def kb_workdir(tenant: str, project: str):
        return f"{workdir}/{tenant}/projects/{project}/knowledge_base"
    project_storage_path = kb_workdir(tenant, project) # "file://./debug_kb_storage"
    processing_mode = "retrieval_only"

    # 1. Validate PDF
    pdf_path = Path(pdf_filepath)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_filepath}")

    print(f"✓ PDF: {pdf_path.name}")

    # 2. Create KB
    tenant_storage_backend = create_storage_backend(workdir)
    # Create TenantProjects singleton
    _tenant_projects = TenantProjects(
        storage_backend=tenant_storage_backend,
        tenant_db=_tenant_db,
        tenant_id=TENANT_ID,
        embedding_model_factory=_embedding_model_without_provider_secret
    )
    logger.info(f"✓ Tenant projects created")
    try:
        _tenant_projects.create_project(project)
    except ValueError as e:
        msg = str(e)
        expected = f"Project '{project}' already exists"
        if msg == expected:
            print("That exact “already exists” error happened.")
            # handle that case…
        else:
            # it was some other ValueError — re-raise
            raise

    project_storage_backend = create_storage_backend(project_storage_path)
    kb = KnowledgeBase(tenant, project, project_storage_backend, embedding_model=await embedding_model(), processing_mode=processing_mode)
    logger.info(f"✓ KB created")

    # 3. Add resource
    with open(pdf_path, 'rb') as f:
        content = f.read()

    element = FileDataElement(
        content=content,
        path=f"debug/{pdf_path.name}",
        filename=pdf_path.name,
        mime="application/pdf"
    )

    resource_metadata = kb.add_resource(element)
    resource_id = resource_metadata.id
    version = resource_metadata.version

    logger.info(f"✓ Resource added: {resource_id}")

    # 4. Process stages
    start_time = time.time()

    logger.info("Processing extraction...")
    await kb.extract_only(resource_id, version)
    logger.info(f"  ✓ Extraction done ({time.time() - start_time:.1f}s)")

    logger.info("Processing segmentation...")
    await kb.process_resource(resource_id, version, stages=["segmentation"])
    logger.info(f"  ✓ Segmentation done ({time.time() - start_time:.1f}s)")

    logger.info("Processing metadata...")

    try:
        await kb.process_resource(resource_id,
                                  version,
                                  stages=["metadata"], stages_config={
                "metadata": {
                    "model_record": await metadata_model(),
                    "use_batch": False
                },
            },
                                  force_reprocess=True)
        logger.info(f"  ✓ Metadata done ({time.time() - start_time:.1f}s)")
    except Exception as e:
        logger.info(f"  ⚠ Metadata failed: {e}")

    from kdcube_ai_app.apps.knowledge_base.modules.contracts.segmentation import SegmentType
    metadata_module = kb.get_metadata_module()
    all_metadata_records = metadata_module.get_resource_records(resource_id, version, SegmentType.RETRIEVAL)
    # 5. Check results
    segmentation_module = kb.get_segmentation_module()
    if segmentation_module:

        retrieval_segments = segmentation_module.get_retrieval_segments(resource_id, version)
        logger.info(f"✓ Created {len(retrieval_segments)} retrieval segments")

        if retrieval_segments:
            sample_text = retrieval_segments[0].get("text", "")[:100]
            logger.info(f"  Sample: {sample_text}...")

    logger.info("Processing embeddings...")
    try:
        await kb.process_resource(resource_id, version, stages=["embedding"],
                                  stages_config={
                                      "embedding": {
                                          "model_record": await embedding_model()
                                      },
                                  }, force_reprocess=True)
        logger.info(f"  ✓ Embeddings done ({time.time() - start_time:.1f}s)")

        # NEW: Verify embeddings were created
        embedding_module = kb.get_embedding_module()
        if embedding_module:
            all_embedding_records = embedding_module.get_resource_records(resource_id, version, SegmentType.RETRIEVAL)
            logger.info(f"✓ Created {len(all_embedding_records)} embeddings")

    except Exception as e:
        logger.info(f"  ⚠ Embeddings failed: {e}")

    logger.info("Processing search indexing...")
    try:
        await kb.process_resource(resource_id, version, stages=["search_indexing"],
                                  stages_config={
                                      "search_indexing": {
                                          "enabled": True
                                      },
                                  }, force_reprocess=True)
        logger.info(f"  ✓ Search indexing done ({time.time() - start_time:.1f}s)")

        # NEW: Verify embeddings were created
        search_indexing_module = kb.get_search_indexing_module()
        if search_indexing_module:
            indexing_results = search_indexing_module.get_indexing_status(resource_id, version)

    except Exception as e:
        logger.info(f"  ⚠ Search indexing failed: {e}")

    # 6. Test search
    try:
        search_results = kb.hybrid_search("uncertainty in llm", resource_id, version, top_k=2)
        logger.info(f"✓ Search test: {len(search_results)} results")
    except Exception as e:
        logger.exception(f"⚠ Search failed: {e}")

    total_time = time.time() - start_time
    logger.info(f"\n🎉 SUCCESS: Pipeline completed in {total_time:.2f}s")

    return {
        "resource_id": resource_id,
        "version": version,
        "duration": total_time
    }


if __name__ == "__main__":

    from dotenv import load_dotenv, find_dotenv

    load_dotenv(find_dotenv())
    # Replace with your PDF path
    pdf_file = "/Users/elenaviter/workdir/expert-factory/projects/hallucinations/materials/inevitable-hallucinations.pdf"
    # workdir = "/Users/elenaviter/src/third/aib/crew-ai/benchmark/libraries/kdcube-ai-app/kdcube_ai_app/apps/knowledge_base/debug/tenants"
    # workdir = "file:///Users/elenaviter/src/third/aib/crew-ai/benchmark/data/kdcube/kb/tenants"
    workdir = f'{os.environ["KDCUBE_STORAGE_PATH"]}/kb/tenants'
    try:
        result = asyncio.run(simple_kb_debug(workdir, pdf_file))
        logger.info(f"Final result: {result}")
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
