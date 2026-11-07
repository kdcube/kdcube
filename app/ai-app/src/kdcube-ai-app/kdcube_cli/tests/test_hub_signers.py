from pathlib import Path

import pytest
import yaml

from kdcube_cli.hub_signers import HUB_ID, SIGNER_REFS, prepare_hub_signing_refs, public_bootstrap_failure


def test_older_descriptor_gets_refs_once_and_keeps_other_config(tmp_path):
    path = tmp_path / "bundles.yaml"
    path.write_text(yaml.safe_dump({"bundles": {"items": [{"id": HUB_ID, "config": {"keep": "value"}}]}}))
    assert prepare_hub_signing_refs(tmp_path)
    before = path.read_bytes()
    assert prepare_hub_signing_refs(tmp_path)
    assert path.read_bytes() == before
    config = yaml.safe_load(before)["bundles"]["items"][0]["config"]
    assert config["keep"] == "value"
    assert config["connections"]["delegated_credentials"]["oauth"]["original_exchange"]["refresh_signing_secret_ref"] == "oauth_original.refresh_signing_secret"


def test_custom_ref_and_explicit_empty_ref_are_never_replaced(tmp_path):
    config = {"connections": {"delegated_credentials": {"oauth": {"original_exchange": {
        "refresh_signing_secret_ref": "custom.refresh",
    }}, "admission": {"services": {"kdcube-management": {"secret_ref": ""}}}}}}
    path = tmp_path / "bundles.yaml"
    path.write_text(yaml.safe_dump({"bundles": {"items": [{"id": HUB_ID, "config": config}]}}))
    before = path.read_bytes()
    prepare_hub_signing_refs(tmp_path)
    assert path.read_bytes() == before


def test_fresh_sdk_skeleton_references_each_signer_slot():
    deployment = Path(__file__).resolve().parents[4] / "deployment"
    bundles = yaml.safe_load((deployment / "bundles.yaml").read_text())
    seeds = yaml.safe_load((deployment / "bundles.secrets.yaml").read_text())
    config = next(item["config"] for item in bundles["bundles"]["items"] if item["id"] == HUB_ID)
    secret = next(item["secrets"] for item in seeds["bundles"]["items"] if item["id"] == HUB_ID)
    paths = [*SIGNER_REFS.values(), "connections.delegated_credentials.grantor_authority_secret"]
    for path in paths:
        node = secret
        for part in path.split("."):
            node = node[part]
        assert isinstance(node, str) and "<" in node
    for dotted, expected in SIGNER_REFS.items():
        node = config
        for part in dotted.split("."):
            node = node[part]
        assert node == expected


@pytest.mark.parametrize("config", ["invalid", {"connections": {"delegated_credentials": []}}])
def test_malformed_descriptor_is_refused_without_rewrite(tmp_path, config):
    path = tmp_path / "bundles.yaml"
    path.write_text(yaml.safe_dump({"bundles": {"items": [{"id": HUB_ID, "config": config}]}}))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        prepare_hub_signing_refs(tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_reference_insertion_preserves_comments_and_other_bundle_bytes(tmp_path, newline):
    text = """# top-level operator comment
bundles:
  items:
  - id: connection-hub@1-0
    config:
      connections:
        delegated_credentials:
          oauth:
            original_exchange:
              keep: true # Hub comment stays
          admission:
            services:
              kdcube-management:
                enabled: true # management comment stays
  # The board entry belongs to its own synchronization tool.
  - id: other-app@1-0
    config: {kept: 'verbatim', spacing: [1,  2]}
# trailing operator comment
""".replace("\n", newline)
    path = tmp_path / "bundles.yaml"
    path.write_bytes(text.encode())
    assert prepare_hub_signing_refs(tmp_path)
    actual = path.read_bytes().decode()
    assert actual.index("refresh_signing_secret_ref:") < actual.index("# The board entry")
    assert actual.index("secret_ref:") < actual.index("# The board entry")
    for line in (
        "              refresh_signing_secret_ref: oauth_original.refresh_signing_secret",
        "                secret_ref: connections.delegated_credentials.admission.services.kdcube-management.signing_secret",
    ):
        assert actual.count(line) == 1
        actual = actual.replace(line + newline, "")
    assert actual == text


def test_flow_mapping_refuses_instead_of_reformatting(tmp_path):
    path = tmp_path / "bundles.yaml"
    before = b"bundles:\n  items:\n  - id: connection-hub@1-0\n    config: {} # keep\n"
    path.write_bytes(before)
    with pytest.raises(ValueError, match="block mappings"):
        prepare_hub_signing_refs(tmp_path)
    assert path.read_bytes() == before


def test_public_failure_reason_filters_raw_provider_output():
    assert public_bootstrap_failure("sensitive-provider-value") == ""
    assert public_bootstrap_failure("Connection Hub signing secret bootstrap refused: arbitrary_secret") == ""
    output = "Connection Hub signing secret bootstrap refused: invalid_signing_seed_length [bundles.connection-hub@1-0.secrets.custom.refresh]"
    assert public_bootstrap_failure(output) == (
        " Reason: invalid_signing_seed_length. Key: bundles.connection-hub@1-0.secrets.custom.refresh."
    )
