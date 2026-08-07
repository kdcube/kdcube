# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The linkedin capability catalog: progressive schema views over one schema."""

from __future__ import annotations

from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import named_service as ns
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.schema_catalog import (
    catalog_operation_entries,
    search_catalog_lexical,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.schema_projection import (
    _validate_projection_index,
    build_schema_tree,
)


@pytest.fixture()
def provider(monkeypatch):
    async def _accounts(**_kwargs):
        return []

    monkeypatch.setattr(ns, "connected_linkedin_accounts", _accounts)
    return ns.LinkedInNamedServiceProvider(bundle_id="kdcube-services@1-0")


@pytest.fixture()
def ctx():
    return NamedServiceContext(tenant="t", project="p", user_id="u1")


async def _schema(provider, ctx, **payload: Any) -> dict[str, Any]:
    response = await provider.dispatch(
        ctx,
        NamedServiceRequest(
            operation="object.schema",
            namespace=ns.LINKEDIN_NAMESPACE,
            object_ref=payload.pop("object_ref", ""),
            payload=payload,
        ),
    )
    return dict((response.ret or {}).get("extra", {}).get("schema") or response.ret or {})


def test_projection_index_matches_the_schema():
    """A mismatched index makes every object.schema call fail with a 500."""
    _validate_projection_index(ns.LINKEDIN_SCHEMA, ns.LINKEDIN_SCHEMA_PROJECTION)


def test_every_declared_action_is_assigned_to_one_kind():
    assigned = [
        action
        for spec in ns.LINKEDIN_SCHEMA_PROJECTION["kinds"].values()
        for action in spec.get("actions", ())
    ]
    assert sorted(assigned) == sorted(ns.LINKEDIN_SCHEMA["actions"])
    assert len(assigned) == len(set(assigned))


@pytest.mark.asyncio
async def test_namespace_only_returns_the_root_catalog(provider, ctx):
    catalog = (await _schema(provider, ctx))["catalog"]
    assert catalog["path"] == "/"
    assert [child["id"] for child in catalog["children"]] == [
        "accounts",
        "publishing",
        "engagement",
    ]


@pytest.mark.asyncio
async def test_schema_path_browses_one_branch_recursively(provider, ctx):
    catalog = (await _schema(provider, ctx, schema_path="/publishing"))["catalog"]
    assert catalog["path"] == "/publishing"
    assert [child["id"] for child in catalog["children"]] == ["text", "images", "staging"]


@pytest.mark.asyncio
async def test_capability_query_selects_the_image_branch_alone(provider, ctx):
    search = (await _schema(provider, ctx, query="publish a LinkedIn post with images"))[
        "catalog_search"
    ]
    paths = [match["catalog_path"] for match in search["matches"]]
    assert paths[0] == "/publishing/images"
    assert "/publishing/text" not in paths
    top = search["matches"][0]
    assert top["object_kind"] == ns.LINKEDIN_ACCOUNT_KIND
    assert top["schema_operation"] == f"object.action:{ns.ACTION_PUBLISH_IMAGE_POST}"


@pytest.mark.asyncio
async def test_capability_search_reports_its_effective_mode(provider, ctx):
    """No embedding service is bound in tests, so hybrid degrades to lexical."""
    search = (await _schema(provider, ctx, query="comment on a post"))["catalog_search"]
    assert search["requested_search_mode"] == "hybrid"
    assert search["effective_search_mode"] == "lexical"
    assert search["degraded_reason"]
    assert search["matches"][0]["catalog_path"] == "/engagement/comment"


@pytest.mark.asyncio
async def test_object_kind_view_lists_the_kind_operations(provider, ctx):
    projected = await _schema(provider, ctx, object_kind=ns.LINKEDIN_POST_KIND)
    available = projected["schema_projection"]["available_operations"]
    assert projected["schema_projection"]["view"] == "kind"
    assert f"object.action:{ns.ACTION_ADD_COMMENT}" in available
    assert f"object.action:{ns.ACTION_PUBLISH_POST}" not in available


@pytest.mark.asyncio
async def test_object_ref_infers_the_kind(provider, ctx):
    projected = await _schema(
        provider, ctx, object_ref="linkedin:acc_1:post:urn:li:share:7123"
    )
    assert projected["schema_projection"]["object_kind"] == ns.LINKEDIN_POST_KIND


@pytest.mark.asyncio
async def test_exact_operation_exposes_payload_and_account_contract(provider, ctx):
    projected = await _schema(
        provider, ctx, schema_operation=f"object.action:{ns.ACTION_PUBLISH_IMAGE_POST}"
    )
    contract = projected["actions"][ns.ACTION_PUBLISH_IMAGE_POST]
    assert contract["payload"]["files"]["required"] is True
    assert "linkedin:<account_id>" in contract["object_ref"]
    # Global sections ride every view so the caller learns the account rules
    # from the declaration rather than from a denial.
    selection = projected["account_selection"]
    assert "account id" in selection["refs"]
    assert selection["unbound_account"]
    # named_services_call leaves object_ref optional and every action payload
    # takes account_id, so the ambiguity path is reachable and must be declared.
    assert "account_required" in selection["action"]
    assert "payload.account_id" in selection["action"]
    assert projected["connected_account_claims"]["post"] == "linkedin:post"


@pytest.mark.asyncio
async def test_full_view_returns_the_whole_schema(provider, ctx):
    projected = await _schema(provider, ctx, schema_view="full")
    assert set(projected["actions"]) == set(ns.LINKEDIN_SCHEMA["actions"])
    assert projected["schema_projection"]["view"] == "full"


@pytest.mark.asyncio
async def test_conflicting_selectors_are_rejected(provider, ctx):
    response = await provider.dispatch(
        ctx,
        NamedServiceRequest(
            operation="object.schema",
            namespace=ns.LINKEDIN_NAMESPACE,
            payload={"schema_view": "full", "query": "publish"},
        ),
    )
    assert response.ok is False
    assert response.error.code == "named_service_schema_view_conflict"


@pytest.mark.asyncio
async def test_unknown_schema_path_is_rejected(provider, ctx):
    response = await provider.dispatch(
        ctx,
        NamedServiceRequest(
            operation="object.schema",
            namespace=ns.LINKEDIN_NAMESPACE,
            payload={"schema_path": "/publishing/video"},
        ),
    )
    assert response.ok is False
    assert response.error.code == "named_service_schema_path_unknown"


def _catalog_tree():
    return build_schema_tree(ns.LINKEDIN_SCHEMA, ns.LINKEDIN_SCHEMA_PROJECTION)


def test_every_declared_keyword_reaches_a_searchable_entry():
    """A keyword on a branch node is never searched.

    catalog_operation_entries attaches a node's terms only to the operations
    that node itself owns, so a keyword on a parent that owns none is dead.
    """
    reachable: set[str] = set()
    for entry in catalog_operation_entries(_catalog_tree()):
        reachable.update(str(term) for term in (entry.get("catalog_terms") or []))

    declared: set[str] = set()

    def _collect(node: Any) -> None:
        if not isinstance(node, dict):
            return
        declared.update(str(word) for word in (node.get("keywords") or []))
        for child in node.get("children") or ():
            _collect(child)

    _collect(_catalog_tree())
    assert declared
    assert sorted(word for word in declared if word not in reachable) == []


@pytest.mark.parametrize(
    ("query", "catalog_path"),
    [
        ("picture", "/publishing/images"),
        ("pictures", "/publishing/images"),
        ("photo", "/publishing/images"),
        ("photos", "/publishing/images"),
        ("galleries", "/publishing/images"),
        ("reply", "/engagement/comment"),
        ("replies", "/engagement/comment"),
        ("uploads", "/publishing/staging"),
        ("profiles", "/accounts/list"),
        ("permalinks", "/engagement/posts"),
    ],
)
def test_both_word_forms_reach_their_branch(query: str, catalog_path: str):
    """The matcher prefix-tests the FIELD token against the query term.

    'galleries' does not start with 'gallery', so neither form covers the
    other and both belong in the declaration.
    """
    matches = search_catalog_lexical(_catalog_tree(), query=query, limit=3)
    assert matches, f"{query!r} matched nothing"
    assert matches[0]["catalog_path"] == catalog_path
