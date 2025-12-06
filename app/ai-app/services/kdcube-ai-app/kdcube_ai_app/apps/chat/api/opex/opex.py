# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/api/opex/opex.py
from typing import Optional, List
import logging, asyncio

from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Request, APIRouter, Query, FastAPI
from contextlib import asynccontextmanager

from kdcube_ai_app.apps.chat.api.resolvers import get_user_session_dependency, auth_without_pressure
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.infra.accounting.calculator import (
    RateCalculator,
    AccountingQuery,
    price_table,
    _calculate_agent_costs
)

"""
OPEX Accounting API

File: api/accounting/opex.py

Provides REST endpoints for querying operational expenditure data
from the accounting system using the RateCalculator.
"""

_scheduler_task: Optional[asyncio.Task] = None
logger = logging.getLogger("OPEX.API")

@asynccontextmanager
async def opex_lifespan(app: FastAPI):
    """
    Router lifespan: start scheduler on startup, stop it on shutdown.
    """
    global _scheduler_task

    import kdcube_ai_app.apps.chat.api.opex.routines as routines
    if _scheduler_task is None:
        _scheduler_task = asyncio.create_task(routines.aggregation_scheduler_loop())
        logger.info("[OPEX Aggregator] Background scheduler task started")

    try:
        yield
    finally:
        if _scheduler_task is not None:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
            logger.info("[OPEX Aggregator] Background scheduler task stopped")
            _scheduler_task = None

# Create router
router = APIRouter(lifespan=opex_lifespan)

# =============================================================================
# Request/Response Models
# =============================================================================

class UsageQueryParams(BaseModel):
    """Base parameters for usage queries"""
    tenant: str = Field(..., description="Tenant ID")
    project: str = Field(..., description="Project ID")
    date_from: str = Field(..., description="Start date (YYYY-MM-DD)")
    date_to: str = Field(..., description="End date (YYYY-MM-DD)")
    app_bundle_id: Optional[str] = Field(None, description="Application bundle ID filter")
    service_types: Optional[List[str]] = Field(None, description="Service types to include (llm, embedding)")
    hard_file_limit: Optional[int] = Field(None, description="Maximum files to scan")

class ConversationQueryParams(UsageQueryParams):
    """Parameters for conversation-specific queries"""
    user_id: str = Field(..., description="User ID")
    conversation_id: str = Field(..., description="Conversation ID")

class TurnQueryParams(ConversationQueryParams):
    """Parameters for turn-specific queries"""
    turn_id: str = Field(..., description="Turn ID")

class AgentQueryParams(UsageQueryParams):
    """Parameters for agent-level queries"""
    user_id: Optional[str] = Field(None, description="Filter by user ID")
    conversation_id: Optional[str] = Field(None, description="Filter by conversation ID")
    turn_id: Optional[str] = Field(None, description="Filter by turn ID")

class UsageResponse(BaseModel):
    """Standard usage response"""
    status: str = "ok"
    total: dict
    rollup: List[dict]
    event_count: int = 0
    cost_estimate: Optional[dict] = None

class UserUsageResponse(BaseModel):
    """Response for per-user usage"""
    status: str = "ok"
    users: dict
    total_users: int
    cost_estimate: Optional[dict] = None

class AgentUsageResponse(BaseModel):
    """Response for agent-level usage"""
    status: str = "ok"
    agents: dict
    total_agents: int
    cost_estimate: Optional[dict] = None

# =============================================================================
# Helper Functions
# =============================================================================

def _get_calculator(request: Request) -> RateCalculator:
    """
    Get or create RateCalculator instance.
    Reuse from app.state if available to avoid recreating storage backend.
    """
    calc = getattr(request.app.state, "accounting_calculator", None)
    if calc:
        return calc

    # Create new calculator
    _settings = get_settings()
    # kdcube_path = os.getenv("KDCUBE_STORAGE_PATH", "file:///tmp/kdcube_data")
    kdcube_path = _settings.STORAGE_PATH or "file:///tmp/kdcube_data"
    backend = create_storage_backend(kdcube_path)
    # RAW under 'accounting', aggregates under 'analytics'
    calc = RateCalculator(
        backend,
        base_path="accounting",
        agg_base="analytics",
    )

    # Cache on app state
    request.app.state.accounting_calculator = calc
    return calc

def _compute_cost_estimate(rollup: List[dict]) -> dict:
    """
    Compute cost estimates from rollup data using price table.
    Returns cost breakdown and total.
    """
    configuration = price_table()
    llm_pricelist = configuration.get("llm", [])
    emb_pricelist = configuration.get("embedding", [])

    def _find_llm_price(provider: str, model: str):
        for p in llm_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    def _find_emb_price(provider: str, model: str):
        for p in emb_pricelist:
            if p.get("provider") == provider and p.get("model") == model:
                return p
        return None

    total_cost = 0.0
    breakdown = []

    for item in rollup:
        service = item.get("service")
        provider = item.get("provider")
        model = item.get("model")
        spent = item.get("spent", {}) or {}

        cost_usd = 0.0

        if service == "llm":
            pr = _find_llm_price(provider, model)
            if pr:
                input_cost = (float(spent.get("input", 0)) / 1_000_000.0) * float(pr.get("input_tokens_1M", 0.0))
                output_cost = (float(spent.get("output", 0)) / 1_000_000.0) * float(pr.get("output_tokens_1M", 0.0))
                cache_read_cost = (float(spent.get("cache_read", 0)) / 1_000_000.0) * float(pr.get("cache_read_tokens_1M", 0.0))

                cache_write_cost = 0.0
                cache_pricing = pr.get("cache_pricing")

                if cache_pricing and isinstance(cache_pricing, dict):
                    cache_5m_tokens = float(spent.get("cache_5m_write", 0))
                    cache_1h_tokens = float(spent.get("cache_1h_write", 0))

                    if cache_5m_tokens > 0:
                        price_5m = float(cache_pricing.get("5m", {}).get("write_tokens_1M", 0.0))
                        cache_write_cost += (cache_5m_tokens / 1_000_000.0) * price_5m

                    if cache_1h_tokens > 0:
                        price_1h = float(cache_pricing.get("1h", {}).get("write_tokens_1M", 0.0))
                        cache_write_cost += (cache_1h_tokens / 1_000_000.0) * price_1h
                else:
                    cache_write_tokens = float(spent.get("cache_creation", 0))
                    cache_write_price = float(pr.get("cache_write_tokens_1M", 0.0))
                    cache_write_cost = (cache_write_tokens / 1_000_000.0) * cache_write_price

                cost_usd = input_cost + output_cost + cache_write_cost + cache_read_cost

        elif service == "embedding":
            pr = _find_emb_price(provider, model)
            if pr:
                cost_usd = (float(spent.get("tokens", 0)) / 1_000_000.0) * float(pr.get("tokens_1M", 0.0))

        total_cost += cost_usd
        breakdown.append({
            "service": service,
            "provider": provider,
            "model": model,
            "cost_usd": cost_usd,
        })

    return {
        "total_cost_usd": total_cost,
        "breakdown": breakdown
    }

# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/total")
async def get_total_usage(
        request: Request,
        tenant: str = Query(..., description="Tenant ID"),
        project: str = Query(..., description="Project ID"),
        date_from: str = Query(..., description="Start date (YYYY-MM-DD)"),
        date_to: str = Query(..., description="End date (YYYY-MM-DD)"),
        app_bundle_id: Optional[str] = Query(None, description="App bundle ID"),
        service_types: Optional[str] = Query(None, description="Comma-separated service types"),
        hard_file_limit: Optional[int] = Query(None, description="Max files to scan"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Query total usage for all users in given timeframe.

    Returns:
        - total: Aggregated usage metrics
        - rollup: Compact breakdown by (service, provider, model)
        - user_count: Number of unique users
        - event_count: Total events processed
        - cost_estimate: Estimated costs based on price table
    """
    try:
        # calc = _get_calculator(request)
        #
        # service_types_list = None
        # if service_types:
        #     service_types_list = [s.strip() for s in service_types.split(",")]
        #
        # result = await calc.usage_all_users(
        #     tenant_id=tenant,
        #     project_id=project,
        #     date_from=date_from,
        #     date_to=date_to,
        #     app_bundle_id=app_bundle_id,
        #     service_types=service_types_list,
        #     hard_file_limit=hard_file_limit,
        #     require_aggregates=True # never triggers a big raw scan
        # )
        #
        # # Add cost estimate
        # cost_estimate = None
        # if result.get("rollup"):
        #     cost_estimate = _compute_cost_estimate(result["rollup"])

        # MOCK
        result = {
            "total": 0,
            "rollup": 0,
            "user_count": 0,
            "event_count": 0
        }
        cost_estimate = 0
        # MOCK

        return {
            "status": "ok",
            "total": result["total"],
            "rollup": result["rollup"],
            "user_count": result["user_count"],
            "event_count": result.get("event_count", 0),
            "cost_estimate": cost_estimate
        }

    except RuntimeError as e:
        # Aggregates missing / incomplete
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception(f"[get_total_usage] {tenant}/{project} failed")
        raise HTTPException(status_code=500, detail=f"Failed to query usage: {str(e)}")

@router.get("/users")
async def get_usage_by_user(
        request: Request,
        tenant: str = Query(..., description="Tenant ID"),
        project: str = Query(..., description="Project ID"),
        date_from: str = Query(..., description="Start date (YYYY-MM-DD)"),
        date_to: str = Query(..., description="End date (YYYY-MM-DD)"),
        app_bundle_id: Optional[str] = Query(None, description="App bundle ID"),
        service_types: Optional[str] = Query(None, description="Comma-separated service types"),
        hard_file_limit: Optional[int] = Query(None, description="Max files to scan"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Query usage broken down by user.

    Returns:
        - users: Dict of user_id -> {total, rollup}
        - total_users: Count of users
        - cost_estimate: Per-user cost estimates
    """
    try:
        # TODO: get back when we have scheduled aggregates!
        # calc = _get_calculator(request)
        #
        # service_types_list = None
        # if service_types:
        #     service_types_list = [s.strip() for s in service_types.split(",")]
        #
        # by_user = await calc.usage_by_user(
        #     tenant_id=tenant,
        #     project_id=project,
        #     date_from=date_from,
        #     date_to=date_to,
        #     app_bundle_id=app_bundle_id,
        #     service_types=service_types_list,
        #     hard_file_limit=hard_file_limit
        # )
        #
        # # Add cost estimates per user
        # user_costs = {}
        # for user_id, user_data in by_user.items():
        #     if user_data.get("rollup"):
        #         user_costs[user_id] = _compute_cost_estimate(user_data["rollup"])

        # END OF TODO: get back when we have scheduled aggregates!
        # MOCK
        by_user = dict()
        user_costs = dict()
        # MOCK

        return {
            "status": "ok",
            "users": by_user,
            "total_users": len(by_user),
            "cost_estimate": user_costs
        }

    except Exception as e:
        logger.exception(f"[get_usage_by_user] {tenant}/{project} failed")
        raise HTTPException(status_code=500, detail=f"Failed to query user usage: {str(e)}")

@router.get("/conversation")
async def get_conversation_usage(
        request: Request,
        tenant: str = Query(..., description="Tenant ID"),
        project: str = Query(..., description="Project ID"),
        user_id: str = Query(..., description="User ID"),
        conversation_id: str = Query(..., description="Conversation ID"),
        date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
        date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
        app_bundle_id: Optional[str] = Query(None, description="App bundle ID"),
        service_types: Optional[str] = Query(None, description="Comma-separated service types"),
        hard_file_limit: Optional[int] = Query(None, description="Max files to scan"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Query usage for a specific conversation.

    Returns:
        - total: Aggregated usage
        - rollup: Compact breakdown
        - turns: Usage grouped by turn_id
        - event_count: Total events
        - cost_estimate: Estimated costs
    """
    try:
        # TODO: get back when we have scheduled aggregates!
        # calc = _get_calculator(request)
        #
        # service_types_list = None
        # if service_types:
        #     service_types_list = [s.strip() for s in service_types.split(",")]
        #
        # result = await calc.usage_user_conversation(
        #     tenant_id=tenant,
        #     project_id=project,
        #     user_id=user_id,
        #     conversation_id=conversation_id,
        #     date_from=date_from,
        #     date_to=date_to,
        #     app_bundle_id=app_bundle_id,
        #     service_types=service_types_list,
        #     hard_file_limit=hard_file_limit
        # )
        #
        # # Add cost estimate
        # cost_estimate = None
        # if result.get("rollup"):
        #     cost_estimate = _compute_cost_estimate(result["rollup"])
        # END OF TODO: get back when we have scheduled aggregates!

        # MOCK
        result = {
            "total": 0,
            "rollup": 0,
            "turns": {},
            "event_count": 0
        }
        cost_estimate = 0
        # MOCK

        return {
            "status": "ok",
            "total": result["total"],
            "rollup": result["rollup"],
            "turns": result.get("turns", {}),
            "event_count": result.get("event_count", 0),
            "cost_estimate": cost_estimate
        }

    except Exception as e:
        logger.exception(f"[get_conversation_usage] {tenant}/{project}/{conversation_id} failed")
        raise HTTPException(status_code=500, detail=f"Failed to query conversation usage: {str(e)}")

@router.get("/turn")
async def get_turn_usage(
        request: Request,
        tenant: str = Query(..., description="Tenant ID"),
        project: str = Query(..., description="Project ID"),
        user_id: str = Query(..., description="User ID"),
        conversation_id: str = Query(..., description="Conversation ID"),
        turn_id: str = Query(..., description="Turn ID"),
        date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
        date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
        app_bundle_id: Optional[str] = Query(None, description="App bundle ID"),
        service_types: Optional[str] = Query(None, description="Comma-separated service types"),
        hard_file_limit: Optional[int] = Query(5000, description="Max files to scan"),
        use_memory_cache: bool = Query(False, description="Use in-memory cache if available"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Query usage for a specific turn (async).

    Returns:
        - turn_id: Turn identifier
        - event_count: Events in this turn
        - total_usage: Aggregated metrics
        - tokens: Total tokens
        - rollup: Compact breakdown by agent
        - cost_estimate: Estimated costs
    """
    try:
        # TODO: get back when we have scheduled aggregates!
        # calc = _get_calculator(request)
        #
        # service_types_list = None
        # if service_types:
        #     service_types_list = [s.strip() for s in service_types.split(",")]
        #
        # # Get basic turn usage
        # turn_result = await calc.query_turn_usage(
        #     tenant_id=tenant,
        #     project_id=project,
        #     conversation_id=conversation_id,
        #     turn_id=turn_id,
        #     user_id=user_id,
        #     app_bundle_id=app_bundle_id,
        #     date_from=date_from,
        #     date_to=date_to,
        #     service_types=service_types_list,
        #     hard_file_limit=hard_file_limit
        # )
        #
        # # Get compact rollup
        # rollup = await calc.turn_usage_rollup_compact(
        #     tenant_id=tenant,
        #     project_id=project,
        #     conversation_id=conversation_id,
        #     turn_id=turn_id,
        #     user_id=user_id,
        #     app_bundle_id=app_bundle_id,
        #     date_from=date_from,
        #     date_to=date_to,
        #     service_types=service_types_list,
        #     hard_file_limit=hard_file_limit,
        #     use_memory_cache=use_memory_cache
        # )
        #
        # # Add cost estimate
        # cost_estimate = None
        # if rollup:
        #     cost_estimate = _compute_cost_estimate(rollup)
        # END OF TODO: get back when we have scheduled aggregates!

        # MOCK
        turn_result = {
            "event_count": 0,
            "total_usage": {},
            "tokens": {}
        }
        rollup = []
        cost_estimate = {}
        # MOCK

        return {
            "status": "ok",
            "turn_id": turn_id,
            "event_count": turn_result["event_count"],
            "total_usage": turn_result["total_usage"],
            "tokens": turn_result["tokens"],
            "rollup": rollup,
            "cost_estimate": cost_estimate
        }

    except Exception as e:
        logger.exception(f"[get_turn_usage] {tenant}/{project}/{turn_id} failed")
        raise HTTPException(status_code=500, detail=f"Failed to query turn usage: {str(e)}")

@router.get("/agents")
async def get_agent_usage(
        request: Request,
        tenant: str = Query(..., description="Tenant ID"),
        project: str = Query(..., description="Project ID"),
        date_from: str = Query(..., description="Start date (YYYY-MM-DD)"),
        date_to: str = Query(..., description="End date (YYYY-MM-DD)"),
        user_id: Optional[str] = Query(None, description="Filter by user ID"),
        conversation_id: Optional[str] = Query(None, description="Filter by conversation ID"),
        turn_id: Optional[str] = Query(None, description="Filter by turn ID"),
        app_bundle_id: Optional[str] = Query(None, description="App bundle ID"),
        service_types: Optional[str] = Query(None, description="Comma-separated service types"),
        hard_file_limit: Optional[int] = Query(None, description="Max files to scan"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Query usage broken down by agent.

    Returns:
        - agents: Dict of agent_name -> {total, rollup}
        - total_agents: Count of agents
        - cost_estimate: Per-agent cost estimates
    """
    try:
        # TODO: get back when we have scheduled aggregates!
        # calc = _get_calculator(request)
        #
        # service_types_list = None
        # if service_types:
        #     service_types_list = [s.strip() for s in service_types.split(",")]
        #
        # by_agent = await calc.usage_by_agent(
        #     tenant_id=tenant,
        #     project_id=project,
        #     date_from=date_from,
        #     date_to=date_to,
        #     user_id=user_id,
        #     conversation_id=conversation_id,
        #     turn_id=turn_id,
        #     app_bundle_id=app_bundle_id,
        #     service_types=service_types_list,
        #     hard_file_limit=hard_file_limit
        # )
        #
        # # Add cost estimates per agent
        # agent_costs = {}
        # for agent_name, agent_data in by_agent.items():
        #     if agent_data.get("rollup"):
        #         agent_costs[agent_name] = _compute_cost_estimate(agent_data["rollup"])
        # END OF TODO: get back when we have scheduled aggregates!

        # MOCK
        by_agent = dict()
        agent_costs = dict()
        # MOCK

        return {
            "status": "ok",
            "agents": by_agent,
            "total_agents": len(by_agent),
            "cost_estimate": agent_costs
        }

    except Exception as e:
        logger.exception(f"[get_agent_usage] {tenant}/{project} failed")
        raise HTTPException(status_code=500, detail=f"Failed to query agent usage: {str(e)}")

@router.get("/turn/by-agent")
async def get_turn_usage_by_agent(
        request: Request,
        tenant: str = Query(..., description="Tenant ID"),
        project: str = Query(..., description="Project ID"),
        conversation_id: str = Query(..., description="Conversation ID"),
        turn_id: str = Query(..., description="Turn ID"),
        user_id: Optional[str] = Query(None, description="User ID"),
        date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
        date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
        app_bundle_id: Optional[str] = Query(None, description="App bundle ID"),
        service_types: Optional[str] = Query(None, description="Comma-separated service types"),
        hard_file_limit: Optional[int] = Query(5000, description="Max files to scan"),
        use_memory_cache: bool = Query(False, description="Use in-memory cache if available"),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Query turn usage broken down by agent (highly optimized with prefix filtering).

    Returns:
        - agents: Dict of agent_name -> List[{service, provider, model, spent}]
        - cost_estimate: Per-agent cost estimates
    """
    try:
        # TODO: get back when we have scheduled aggregates!
        # calc = _get_calculator(request)
        #
        # service_types_list = None
        # if service_types:
        #     service_types_list = [s.strip() for s in service_types.split(",")]
        #
        # by_agent = await calc.turn_usage_by_agent(
        #     tenant_id=tenant,
        #     project_id=project,
        #     conversation_id=conversation_id,
        #     turn_id=turn_id,
        #     user_id=user_id,
        #     app_bundle_id=app_bundle_id,
        #     date_from=date_from,
        #     date_to=date_to,
        #     service_types=service_types_list,
        #     hard_file_limit=hard_file_limit,
        #     use_memory_cache=use_memory_cache
        # )
        #
        # # Calculate costs per agent
        # configuration = price_table()
        # llm_pricelist = configuration.get("llm", [])
        # emb_pricelist = configuration.get("embedding", [])
        #
        # agent_costs = _calculate_agent_costs(by_agent, llm_pricelist, emb_pricelist)
        # END OF TODO: get back when we have scheduled aggregates!

        # MOCK
        by_agent = dict()
        agent_costs = dict()
        # MOCK

        return {
            "status": "ok",
            "turn_id": turn_id,
            "agents": by_agent,
            "total_agents": len(by_agent),
            "cost_estimate": agent_costs
        }

    except Exception as e:
        logger.exception(f"[get_turn_usage_by_agent] {tenant}/{project}/{turn_id} failed")
        raise HTTPException(status_code=500, detail=f"Failed to query turn agent usage: {str(e)}")

@router.get("/health")
async def health_check(
        request: Request,
        session: UserSession = Depends(get_user_session_dependency())
):
    """Health check endpoint for accounting API"""
    try:
        calc = _get_calculator(request)
        return {
            "status": "ok",
            "service": "accounting",
            "calculator": "ready",
            "backend": calc.fs.__class__.__name__
        }
    except Exception as e:
        logger.exception("[health_check] failed")
        return {
            "status": "error",
            "service": "accounting",
            "error": str(e)
        }

# =============================================================================
# Admin Endpoints
# =============================================================================

@router.post("/admin/clear-cache")
async def admin_clear_calculator_cache(
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Clear cached calculator instance (forces recreation with fresh backend).
    Useful after storage configuration changes.
    """
    try:
        if hasattr(request.app.state, "accounting_calculator"):
            delattr(request.app.state, "accounting_calculator")

        return {
            "status": "ok",
            "message": "Calculator cache cleared"
        }
    except Exception as e:
        logger.exception("[admin_clear_calculator_cache] failed")
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")

@router.get("/admin/price-table")
async def admin_get_price_table(
        request: Request,
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Get current price table configuration.
    """
    try:
        return {
            "status": "ok",
            "price_table": price_table()
        }
    except Exception as e:
        logger.exception("[admin_get_price_table] failed")
        raise HTTPException(status_code=500, detail=f"Failed to get price table: {str(e)}")

@router.post("/admin/run-aggregation-range")
async def admin_run_aggregation_range(
        start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
        end_date: Optional[str] = Query(
            None,
            description="End date (YYYY-MM-DD, inclusive). "
                        "If omitted, defaults to yesterday in Europe/Berlin."
        ),
        session: UserSession = Depends(auth_without_pressure())
):
    """
    Manually backfill daily + monthly aggregates for a date range.

    This uses the same Redis locking as the scheduler, so it is safe to call
    on multiple API instances concurrently.

    Examples:
      - /admin/run-aggregation-range?start_date=2025-01-01
        -> backfills from 2025-01-01 up to yesterday.
      - /admin/run-aggregation-range?start_date=2025-01-01&end_date=2025-01-10
        -> backfills 2025-01-01 .. 2025-01-10.
    """

    import kdcube_ai_app.apps.chat.api.opex.routines as routines
    from datetime import datetime, timedelta, date

    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid start_date, expected format YYYY-MM-DD",
        )

    if end_date:
        try:
            end = date.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid end_date, expected format YYYY-MM-DD",
            )
    else:
        # default: yesterday in ACCOUNTING_TZ
        end = (datetime.now(routines.ACCOUNTING_TZ) - timedelta(days=1)).date()

    if end < start:
        raise HTTPException(
            status_code=400,
            detail="end_date must be greater than or equal to start_date",
        )

    # This will loop date-by-date and use Redis locks inside.
    await routines.run_aggregation_range(start, end)

    return {
        "status": "ok",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "message": "Aggregation triggered for date range",
    }