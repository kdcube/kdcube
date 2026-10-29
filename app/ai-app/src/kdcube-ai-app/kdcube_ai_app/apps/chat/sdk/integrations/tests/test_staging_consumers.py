"""Every authenticated staged-file consumer preserves the slot owner boundary."""
import asyncio
import inspect

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations import file_staging
from kdcube_ai_app.apps.chat.sdk.integrations.docs.named_service import DocsNamedServiceProvider
from kdcube_ai_app.apps.chat.sdk.integrations.inline_files import InlineFileError, resolve_payload_file_entries
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import tools as linkedin_tools
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin.named_service import LinkedInNamedServiceProvider
from kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service import MailNamedServiceProvider
from kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service import SlackNamedServiceProvider
from kdcube_ai_app.apps.chat.sdk.integrations.tests.test_file_staging import call
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext, NamedServiceRequest


OWNER = NamedServiceContext(user_id="user-a", tenant="tenant-a", project="project-a")
OTHER = NamedServiceContext(user_id="user-b", tenant="tenant-a", project="project-a")


@pytest.mark.parametrize("namespace,provider_type", [
    ("mail", MailNamedServiceProvider),
    ("slack", SlackNamedServiceProvider),
    ("linkedin", LinkedInNamedServiceProvider),
])
def test_discard_by_another_user_preserves_the_owned_slot(tmp_path, namespace, provider_type):
    ref = file_staging.new_staged_ref("image.png")
    target = call(file_staging.save_staged, tmp_path, ref, b"owned bytes", owner=OWNER)
    provider = provider_type(bundle_id="services")
    provider._staging_root = lambda: tmp_path
    request = NamedServiceRequest(namespace=namespace, operation="object.action", action="discard_upload", payload={"staged_ref": ref})
    provider._discard_upload(OTHER, request)
    assert target.read_bytes() == b"owned bytes"
    provider._discard_upload(OWNER, request)
    assert not target.parent.exists()


def test_inline_file_resolution_refuses_another_tenant(tmp_path):
    ref = file_staging.new_staged_ref("image.png")
    call(file_staging.save_staged, tmp_path, ref, b"private bytes", owner=OWNER)
    other_tenant = NamedServiceContext(user_id="user-a", tenant="tenant-b", project="project-a")
    with pytest.raises(InlineFileError):
        call(resolve_payload_file_entries, [{"staged_ref": ref}], staging_root=tmp_path, owner=other_tenant)


def test_docs_refuses_another_owner_before_media_validation(tmp_path, monkeypatch):
    from kdcube_ai_app.infra.service_hub import multimodality

    ref = file_staging.new_staged_ref("image.png")
    call(file_staging.save_staged, tmp_path, ref, b"private bytes", owner=OWNER)
    def forbidden(*args, **kwargs):
        pytest.fail("another user's bytes must not reach media validation or provider hosting")
    monkeypatch.setattr(multimodality, "validate_image_bytes", forbidden)
    provider = DocsNamedServiceProvider(execute_operation=forbidden, staging_root_factory=lambda: tmp_path, provider_fetch_url_factory=forbidden)
    request = NamedServiceRequest(namespace="docs", operation="object.action", action="embed_image", object_ref="docs:google:account:document:doc")
    _, response = asyncio.run(provider._image_uri_for_file_ref(OTHER, request, {"file_ref": ref}))
    assert response.error.code == "docs_image_file_missing"


def test_linkedin_tool_staged_read_requires_the_bound_owner(tmp_path, monkeypatch):
    ref = file_staging.new_staged_ref("image.png")
    call(file_staging.save_staged, tmp_path, ref, b"private bytes", owner=OWNER)
    monkeypatch.setattr(linkedin_tools, "staging_root_for_service", lambda: tmp_path)
    monkeypatch.setattr(linkedin_tools, "validate_image", lambda *args: pytest.fail("another user's bytes must not be inspected"))
    keywords = {"owner": OTHER} if "owner" in inspect.signature(linkedin_tools.load_staged_image).parameters else {}
    image, error = linkedin_tools.load_staged_image(ref, **keywords)
    assert image is None and error["code"] == "staged_file_missing"
