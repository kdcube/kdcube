# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""A profile scope is judged by the grants its operations need (W272).

On 2026-09-24 the operator re-authorized a worker with
`pb worker authorize <profile> --replace-card`, which requests the scope
`work:profile:worker`. The authorize route checked that selector as if it were
a grant the person holds and refused:
`user is not allowed to delegate the requested grant(s)`, grants
`["work:profile:worker"]`. A profile names catalog operations, so the check
must run on the grants those operations require.
"""

from __future__ import annotations

from connection_hub.delegated_credentials.oauth.config import (
    oauth_delegated_config_from_connections,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes

RESOURCE = "https://runtime.example.test/mcp/problem-board"


def _config():
    return oauth_delegated_config_from_connections(
        {
            "delegated_credentials": {
                "oauth": {
                    "enabled": True,
                    "capabilities": [
                        {"grant": "work:relay", "label": "Relay"},
                        {"grant": "work:coordinate", "label": "Coordinate"},
                    ],
                    "resources": [
                        {
                            "resource": RESOURCE,
                            "tools": {
                                "worker.publish": {"grants": ["work:relay"]},
                                "assignment.assign": {"grants": ["work:coordinate"]},
                            },
                            "authorization_profiles": {
                                "worker": {
                                    "scope": "work:profile:worker",
                                    "label": "Problem Board worker",
                                    "operations": ["worker.publish"],
                                },
                                "coordinator": {
                                    "scope": "work:profile:coordinator",
                                    "label": "Problem Board coordinator",
                                    "operations": ["*"],
                                },
                            },
                        }
                    ],
                }
            }
        }
    )


class _Inventory:
    def __init__(self, *grants: str) -> None:
        self._grants = tuple(grants)

    def grant_names(self):
        return self._grants


def test_a_profile_scope_expands_to_the_grants_its_operations_need():
    cfg = _config()
    assert routes._delegation_grants(cfg, ["work:profile:worker"]) == ["work:relay"]
    assert routes._delegation_grants(cfg, ["work:profile:coordinator"]) == [
        "work:relay",
        "work:coordinate",
    ]
    # A plain grant request is unchanged.
    assert routes._delegation_grants(cfg, ["work:relay"]) == ["work:relay"]


def test_a_worker_profile_is_delegable_by_a_person_who_holds_its_real_grants():
    cfg = _config()
    inventory = _Inventory("work:relay")
    grants = routes._delegation_grants(cfg, ["work:profile:worker"])

    assert routes._delegation_denial(grants, inventory, resource="") is None
    assert routes._visible_scopes(cfg, ["work:profile:worker"], inventory) == [
        "work:profile:worker"
    ]
    # The coordinator profile needs work:coordinate, which this person lacks.
    assert routes._visible_scopes(cfg, ["work:profile:coordinator"], inventory) == []


def test_a_refusal_names_the_real_grant_and_never_the_profile_selector():
    cfg = _config()
    grants = routes._delegation_grants(cfg, ["work:profile:worker"])
    denial = routes._delegation_denial(grants, _Inventory(), resource="")

    assert denial is not None
    body = denial.body.decode("utf-8")
    assert '"work:relay"' in body
    assert "work:profile:worker" not in body


def test_a_tool_without_grants_of_its_own_expands_to_its_resource_grants():
    """Connection Hub runs such a tool under its resource's grants, so the check must too."""
    cfg = oauth_delegated_config_from_connections(
        {
            "delegated_credentials": {
                "oauth": {
                    "enabled": True,
                    "capabilities": [{"grant": "records:read", "label": "Read"}],
                    "resources": [
                        {
                            "resource": RESOURCE,
                            "tools": {"records.list": {}},
                            "authorization_profiles": {
                                "reader": {
                                    "scope": "records:profile:reader",
                                    "label": "Reader",
                                    "operations": ["records.list"],
                                }
                            },
                        }
                    ],
                }
            }
        }
    )
    assert routes._delegation_grants(cfg, ["records:profile:reader"]) == ["records:read"]
    assert routes._visible_scopes(cfg, ["records:profile:reader"], _Inventory()) == []


def test_a_profile_request_reviews_only_the_services_that_declare_it():
    """2026-09-24: `pb worker authorize --replace-card` names no service, and
    the consent page offered "All platform and application APIs" beside
    Problem Board. The operator: "why its here even? it should not be here!!
    only this matters". A profile request reviews the declaring services."""
    cfg = oauth_delegated_config_from_connections(
        {
            "delegated_credentials": {
                "oauth": {
                    "enabled": True,
                    "capabilities": [{"grant": "work:relay", "label": "Relay"}],
                    "resources": [
                        {"resource": "*", "grants": ["platform:use"]},
                        {"resource": "https://other.example.test/mcp", "grants": ["work:relay"]},
                        {
                            "resource": RESOURCE,
                            "label": "Problem Board",
                            "tools": {"worker.publish": {"grants": ["work:relay"]}},
                            "authorization_profiles": {
                                "worker": {
                                    "scope": "work:profile:worker",
                                    "label": "Problem Board worker",
                                    "operations": ["worker.publish"],
                                }
                            },
                        },
                    ],
                }
            }
        }
    )
    full = {"mode": "full", "resources": []}

    assert routes._profile_catalog_scope(cfg, ["work:profile:worker"], full) == {
        "mode": "entry",
        "resources": [RESOURCE],
    }
    # A plain grant request keeps the catalog it was seeded with.
    assert routes._profile_catalog_scope(cfg, ["work:relay"], full) == full
    # A request through one named door is already bounded by that door.
    entry = {"mode": "entry", "resources": [RESOURCE]}
    assert routes._profile_catalog_scope(cfg, ["work:profile:worker"], entry) == entry
