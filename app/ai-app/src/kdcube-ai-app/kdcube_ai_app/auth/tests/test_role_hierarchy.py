# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

import pytest

from kdcube_ai_app.auth.AuthManager import RequireRoles, User
from kdcube_ai_app.auth.role_hierarchy import (
    ADMIN_ROLE,
    PAID_ROLE,
    PRIVILEGED_ROLE,
    REGISTERED_ROLE,
    SUPER_ADMIN_ROLE,
    role_satisfies,
    roles_satisfy_all,
    roles_satisfy_any,
    strongest_platform_role,
)


@pytest.mark.parametrize(
    ("actual", "required", "expected"),
    [
        (SUPER_ADMIN_ROLE, SUPER_ADMIN_ROLE, True),
        (SUPER_ADMIN_ROLE, PRIVILEGED_ROLE, True),
        (SUPER_ADMIN_ROLE, PAID_ROLE, True),
        (SUPER_ADMIN_ROLE, REGISTERED_ROLE, True),
        (PRIVILEGED_ROLE, SUPER_ADMIN_ROLE, False),
        (PRIVILEGED_ROLE, PAID_ROLE, True),
        (PRIVILEGED_ROLE, REGISTERED_ROLE, True),
        (PAID_ROLE, PRIVILEGED_ROLE, False),
        (PAID_ROLE, REGISTERED_ROLE, True),
        (REGISTERED_ROLE, PAID_ROLE, False),
    ],
)
def test_platform_role_dominance(actual, required, expected):
    assert role_satisfies(actual, required) is expected


def test_legacy_admin_is_the_privileged_tier():
    assert role_satisfies(ADMIN_ROLE, PRIVILEGED_ROLE) is True
    assert role_satisfies(PRIVILEGED_ROLE, ADMIN_ROLE) is True
    assert role_satisfies(SUPER_ADMIN_ROLE, ADMIN_ROLE) is True
    assert role_satisfies(ADMIN_ROLE, SUPER_ADMIN_ROLE) is False


def test_strongest_platform_role_ignores_unordered_custom_capabilities():
    assert strongest_platform_role(
        [REGISTERED_ROLE, "kdcube:role:finance", SUPER_ADMIN_ROLE]
    ) == SUPER_ADMIN_ROLE
    assert strongest_platform_role(["kdcube:role:finance"]) == ""


def test_unknown_and_service_roles_remain_exact_capabilities():
    finance = "kdcube:role:finance"
    service = "kdcube:role:service"

    assert role_satisfies(SUPER_ADMIN_ROLE, finance) is False
    assert role_satisfies(SUPER_ADMIN_ROLE, service) is False
    assert role_satisfies(finance, finance) is True
    assert roles_satisfy_any([SUPER_ADMIN_ROLE, finance], [finance]) is True
    assert roles_satisfy_all([SUPER_ADMIN_ROLE], [REGISTERED_ROLE, finance]) is False


def test_require_roles_uses_dominance_for_all_and_any_admission():
    privileged = User(username="privileged", roles=[PRIVILEGED_ROLE])
    registered = User(username="registered", roles=[REGISTERED_ROLE])

    assert (
        RequireRoles(REGISTERED_ROLE, PAID_ROLE).validate_requirement(privileged)
        is None
    )
    assert (
        RequireRoles(SUPER_ADMIN_ROLE, PAID_ROLE, require_all=False)
        .validate_requirement(privileged)
        is None
    )
    assert RequireRoles(PAID_ROLE).validate_requirement(registered) is not None


def test_require_roles_does_not_infer_custom_authority():
    super_admin = User(username="admin", roles=[SUPER_ADMIN_ROLE])

    error = RequireRoles("kdcube:role:service").validate_requirement(super_admin)

    assert error is not None
    assert "kdcube:role:service" in error.message
