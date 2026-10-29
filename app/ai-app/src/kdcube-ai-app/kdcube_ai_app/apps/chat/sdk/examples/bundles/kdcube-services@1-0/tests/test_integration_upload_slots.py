"""Execute upload methods with real slot storage and signed-token verification."""
import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations import file_staging as staging
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.download_links import mint_file_download_token, verify_file_download_token


BUNDLE = Path(__file__).resolve().parents[1]
SECRET = "fixture-upload-signing-secret"
OWNER = SimpleNamespace(user_id="user-a", tenant="tenant-a", project="project-a")


def upload_methods(tmp_path):
    # Exercise the actual method bodies with host ports supplied explicitly.
    # The full app's heavy startup graph is outside this storage contract test.
    source = BUNDLE / "entrypoint.py"
    tree = ast.parse(source.read_text())
    entry = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "KDCubeServicesEntrypoint")
    methods = [n for n in entry.body if isinstance(n, ast.AsyncFunctionDef) and n.name in {"_integration_upload_slot", "integration_file_upload"}]
    assert len(methods) == 2
    for method in methods:
        method.decorator_list = []
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *methods], type_ignores=[]))
    namespace = {
        "get_public_base_url": lambda: "https://runtime.example",
        "new_staged_ref": staging.new_staged_ref,
        "create_slot": getattr(staging, "create_slot", None),
        "delete_staged": staging.delete_staged,
        "save_staged": staging.save_staged,
        "staging_root": staging.staging_root,
        "MAX_STAGED_FILE_BYTES": staging.MAX_STAGED_FILE_BYTES,
        "mint_file_download_token": mint_file_download_token,
        "verify_file_download_token": verify_file_download_token,
        "bundle_operation_url": lambda **kw: "https://runtime.example/upload?" + urlencode(kw["query"]),
        "LOGGER": logging.getLogger(__name__),
        "CONV_FILE_DOWNLOAD_SECRET_KEY": "fixture-signing-reference",
    }
    exec(compile(module, str(source), "exec"), namespace)
    async def secret():
        return SECRET
    app = SimpleNamespace(settings=SimpleNamespace(STORAGE_PATH=str(tmp_path)), _conv_download_secret=secret, _named_services_bundle_id=lambda: "services")
    return app, namespace


def request(data):
    async def body():
        return data
    return SimpleNamespace(query_params={}, body=body)


def test_upload_token_carries_only_reference_expiry_and_version(tmp_path):
    app, methods = upload_methods(tmp_path)
    slot = asyncio.run(methods["_integration_upload_slot"](app, OWNER, {"filename": "image.png", "user_id": "untrusted"}))
    assert slot is not None
    token = parse_qs(urlparse(slot["upload_url"]).query)["upload_token"][0]
    claims = verify_file_download_token(SECRET, token, fi_ref=slot["staged_ref"], require_user_scope=False)
    assert set(claims) == {"fi_ref", "exp", "v"}
    identity, _ = staging.parse_staged_ref(slot["staged_ref"])
    record = json.loads((staging.staging_root(str(tmp_path)) / identity / ".owner.json").read_text())
    assert (record["user"], record["tenant"], record["project"]) == (OWNER.user_id, OWNER.tenant, OWNER.project)


def test_an_identity_free_token_uploads_into_its_precreated_slot(tmp_path):
    app, methods = upload_methods(tmp_path)
    slot = asyncio.run(methods["_integration_upload_slot"](app, OWNER, {"filename": "image.png"}))
    assert slot is not None
    token = parse_qs(urlparse(slot["upload_url"]).query)["upload_token"][0]
    response = asyncio.run(methods["integration_file_upload"](app, request(b"image bytes"), object_ref=slot["staged_ref"], upload_token=token))
    assert response.status_code == 200
    assert staging.load_staged(staging.staging_root(str(tmp_path)), slot["staged_ref"], owner=OWNER) == ("image.png", b"image bytes")


def test_a_signed_token_cannot_create_a_slot_that_was_never_requested(tmp_path):
    app, methods = upload_methods(tmp_path)
    ref = staging.new_staged_ref("image.png")
    # A signed legacy-shaped token still cannot confer slot-creation authority.
    token, _ = mint_file_download_token(SECRET, fi_ref=ref, user_id=OWNER.user_id)
    response = asyncio.run(methods["integration_file_upload"](app, request(b"image bytes"), object_ref=ref, upload_token=token))
    assert response.status_code == 404
    identity, _ = staging.parse_staged_ref(ref)
    assert not (staging.staging_root(str(tmp_path)) / identity).exists()


@pytest.mark.parametrize("field", ["user_id", "tenant", "project"])
def test_unbound_identity_cannot_request_an_upload_slot(tmp_path, field):
    app, methods = upload_methods(tmp_path)
    owner = SimpleNamespace(**{**vars(OWNER), field: ""})
    slot = asyncio.run(methods["_integration_upload_slot"](app, owner, {"filename": "image.png"}))
    assert slot is None


def test_upload_refuses_another_refs_token(tmp_path):
    app, methods = upload_methods(tmp_path)
    ref = staging.new_staged_ref("image.png")
    token, _ = mint_file_download_token(SECRET, fi_ref=staging.new_staged_ref("other.png"), user_id="", include_identity=False)
    response = asyncio.run(methods["integration_file_upload"](app, request(b"bytes"), object_ref=ref, upload_token=token))
    assert response.status_code == 403


def test_bundle_staging_calls_make_their_owner_boundary_explicit():
    for relative in ("entrypoint.py", "services/provider_fetch.py", "services/telegram/notify.py"):
        tree = ast.parse((BUNDLE / relative).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in {"load_staged", "save_staged", "delete_staged"}:
                continue
            owner = next((kw.value for kw in node.keywords if kw.arg == "owner"), None)
            assert owner is not None, f"{relative}:{node.lineno} omits staging owner"
            if isinstance(owner, ast.Constant) and owner.value is None:
                assert (relative, node.func.id) in {("entrypoint.py", "save_staged"), ("services/provider_fetch.py", "load_staged")}
