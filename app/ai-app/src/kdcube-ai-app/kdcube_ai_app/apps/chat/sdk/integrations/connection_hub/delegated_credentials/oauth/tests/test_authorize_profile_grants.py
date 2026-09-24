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

import json
import os
import re
import urllib.parse as up

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from connection_hub.delegated_credentials.cards.store import (
    BundleStorageDelegatedCardStore,
    subject_hash_for,
)
from connection_hub.delegated_credentials.oauth.consent import (
    CONSENT_CONTRACT_VERSION,
)
from connection_hub.delegated_credentials.oauth.config import (
    oauth_delegated_config_from_connections,
)
from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from connection_hub.delegated_credentials.oauth.store import GrantStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.helpers import (
    bind_delegated_card_persistence,
    bind_delegated_catalog,
    enable_delegated_client,
    mount_test_oauth_adapter,
    publish_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_clients_and_store import (
    FakeRedis,
)

RESOURCE = "https://runtime.example.test/mcp/problem-board"
SECOND_RESOURCE = "https://runtime.example.test/mcp/other-service"
ISSUER = "https://connector.example.test"
VERIFIER = "code-verifier-" + "z" * 60
CHALLENGE = make_s256_challenge(VERIFIER)


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


def test_a_resource_scoped_profile_expands_only_the_matched_catalog_row():
    cfg = oauth_delegated_config_from_connections(
        {
            "delegated_credentials": {
                "oauth": {
                    "enabled": True,
                    "capabilities": [
                        {"grant": "first:read", "label": "Read first"},
                        {"grant": "second:write", "label": "Write second"},
                    ],
                    "resources": [
                        {
                            "resource": RESOURCE,
                            "tools": {
                                "first.read": {"grants": ["first:read"]},
                            },
                            "authorization_profiles": {
                                "worker": {
                                    "scope": "work:profile:worker",
                                    "label": "Worker",
                                    "operations": ["*"],
                                }
                            },
                        },
                        {
                            "resource": SECOND_RESOURCE,
                            "tools": {
                                "second.write": {"grants": ["second:write"]},
                            },
                            "authorization_profiles": {
                                "worker": {
                                    "scope": "work:profile:worker",
                                    "label": "Worker",
                                    "operations": ["*"],
                                }
                            },
                        },
                    ],
                }
            }
        }
    )

    assert routes._delegation_grants(
        cfg,
        ["work:profile:worker"],
        resource=RESOURCE,
    ) == ["first:read"]
    assert routes._delegation_grants(
        cfg,
        ["work:profile:worker"],
        resource=SECOND_RESOURCE,
    ) == ["second:write"]
    assert routes._delegation_grants(cfg, ["work:profile:worker"]) == [
        "first:read",
        "second:write",
    ]


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


def _profile_oauth_config() -> dict:
    return {
        "enabled": True,
        "issuer": ISSUER,
        "capabilities": [
            {
                "grant": "work:relay",
                "label": "Relay work",
                "delegable_roles": ["kdcube:role:super-admin"],
            }
        ],
        "resources": [
            {
                "resource": RESOURCE,
                "grants": ["work:relay"],
                "tools": {
                    "worker.publish": {
                        "label": "Publish worker state",
                        "grants": ["work:relay"],
                    }
                },
                "authorization_profiles": {
                    "worker": {
                        "scope": "work:profile:worker",
                        "label": "Problem Board worker",
                        "operations": ["worker.publish"],
                    }
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_classic_profile_consent_persists_real_grants_through_refresh(
    tmp_path,
):
    if not os.environ.get("REDIS_URL"):
        pytest.skip("REDIS_URL is not set; token issuance commits a delegated card")

    import redis.asyncio as redis_asyncio

    config = _profile_oauth_config()
    app = FastAPI()
    enable_delegated_client(app, issuer=ISSUER)
    publish_delegated_config(app, config)
    card_store = BundleStorageDelegatedCardStore(tmp_path)
    bind_delegated_catalog(
        app,
        {"delegated_credentials": {"oauth": config}},
        cards=card_store,
    )
    store = GrantStore(FakeRedis(), tenant="home", project="demo")
    app.state.oauth_grant_store = store

    async def authenticate(token):
        if token == "admin-tok":
            return {
                "sub": "google:admin@example.test",
                "roles": ["kdcube:role:super-admin"],
            }
        return None

    mint_count = 0

    async def mint_access_token(sub, scopes, **_kwargs):
        nonlocal mint_count
        mint_count += 1
        return {
            "access_token": f"kst1.mock.{mint_count}.{sub}",
            "expires_in": 3600,
        }

    app.state.oauth_authenticate = authenticate
    app.state.oauth_mint_access_token = mint_access_token
    mount_test_oauth_adapter(app)
    redis = redis_asyncio.from_url(os.environ["REDIS_URL"])
    app.add_event_handler("shutdown", redis.aclose)
    bind_delegated_card_persistence(app, redis=redis, storage_root=tmp_path)

    params = {
        "client_id": "claude",
        "redirect_uri": "http://127.0.0.1:9876/callback",
        "response_type": "code",
        "scope": "work:profile:worker",
        "resource": RESOURCE,
        "state": "profile-state",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    with TestClient(app) as client:
        page = client.get(
            "/oauth/authorize",
            params=params,
            headers={"Authorization": "Bearer admin-tok"},
        )
        assert page.status_code == 200, page.text
        csrf = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            page.text,
        )
        assert csrf is not None

        consent = client.post(
            "/oauth/authorize/consent",
            data={
                **params,
                "decision": "approve",
                "consent_contract_version": CONSENT_CONTRACT_VERSION,
                "platform_grants": ["work:profile:worker"],
                "tools": ["worker.publish"],
                "csrf_token": csrf.group(1),
            },
            headers={"Authorization": "Bearer admin-tok"},
            follow_redirects=False,
        )
        assert consent.status_code == 302, consent.text
        code = dict(up.parse_qsl(up.urlsplit(consent.headers["location"]).query))[
            "code"
        ]
        code_payload = json.loads(store._r.values[store._key("code", code)])
        assert code_payload["scopes"] == ["work:relay"]
        assert code_payload["resource_grants"] == {RESOURCE: ["work:relay"]}
        assert code_payload["delegation_edges"][0]["grants"] == ["work:relay"]
        assert "work:profile:worker" not in json.dumps(code_payload)

        exchanged = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": params["redirect_uri"],
                "client_id": "claude",
                "code_verifier": VERIFIER,
            },
        )
        assert exchanged.status_code == 200, exchanged.text
        first = exchanged.json()
        assert first["scope"] == "work:relay"

        access_record = await store.get_access_grant_record(first["access_token"])
        refresh_record = await store.validate_refresh_token(first["refresh_token"])
        assert access_record["resource_grants"] == {RESOURCE: ["work:relay"]}
        assert refresh_record["scopes"] == ["work:relay"]
        assert refresh_record["delegation_edges"][0]["grants"] == ["work:relay"]

        stored = await card_store.read_current_authority(
            subject_hash=subject_hash_for("google:admin@example.test"),
            access_id=first["access_id"],
        )
        assert stored is not None
        _pointer, authority = stored
        assert authority.resource_grants == {RESOURCE: ("work:relay",)}
        assert authority.operations == ("worker.publish",)

        refreshed = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": "claude",
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        second = refreshed.json()
        assert second["scope"] == "work:relay"
        refreshed_record = await store.validate_refresh_token(
            second["refresh_token"]
        )
        assert refreshed_record["resource_grants"] == {
            RESOURCE: ["work:relay"]
        }
        assert refreshed_record["delegation_edges"][0]["grants"] == [
            "work:relay"
        ]
