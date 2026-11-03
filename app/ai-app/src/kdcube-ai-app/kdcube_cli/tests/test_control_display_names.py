import pytest

from kdcube_cli.control.models import DeploymentTargetRef
from kdcube_cli.control.surfaces import application_inventory


@pytest.mark.parametrize("fields,expected", [
    ({}, "custom-app@1-0"),
    ({"name": None}, "custom-app@1-0"),
    ({"name": ""}, "custom-app@1-0"),
    ({"name": " \t\n"}, "custom-app@1-0"),
    ({"name": "Custom App"}, "Custom App"),
])
def test_inventory_labels_are_available_without_loading_the_sdk(tmp_path, fields, expected):
    target = DeploymentTargetRef.local(tmp_path, tenant="tenant", project="project")
    descriptor = {"bundles": {"items": [{"id": "custom-app@1-0", **fields}]}}
    applications, _default = application_inventory(descriptor, target, endpoint="https://runtime.example")
    assert len(applications) == 1
    assert applications[0].name == expected
    assert applications[0].reference.bundle_id == "custom-app@1-0"
