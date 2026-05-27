# SPDX-License-Identifier: MIT

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.ingress.resources import resources
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.auth.sessions import UserSession, UserType


router = resources.router


def test_attachment_resource_urls_accept_nested_artifact_paths():
    path = router.url_path_for(
        "download_cb_attachment",
        tenant="tenant",
        project="project",
        owner_id="user",
        conversation_id="conversation",
        turn_id="turn_1",
        filename="turn_1/outputs/analysis/report.xlsx",
    )

    assert str(path) == (
        "/tenant/project/conv/user/conversation/turn/turn_1/"
        "attachment/turn_1/outputs/analysis/report.xlsx/download"
    )


@pytest.mark.asyncio
async def test_by_rn_resolves_nested_artifact_paths_and_legacy_unescaped_rns(tmp_path, monkeypatch):
    storage_uri = tmp_path.as_uri()
    store = ConversationStore(storage_uri=storage_uri)
    _uri, _key, rn = await store.put_artifact_file(
        tenant="tenant",
        project="project",
        user="user",
        fingerprint=None,
        conversation_id="conversation",
        turn_id="turn_1",
        relpath="turn_1/outputs/analysis/report.xlsx",
        data=b"xlsx-bytes",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    legacy_rn = rn.replace(
        "turn_1%2Foutputs%2Fanalysis%2Freport.xlsx",
        "turn_1/outputs/analysis/report.xlsx",
    )

    monkeypatch.setattr(
        resources,
        "get_settings",
        lambda: SimpleNamespace(STORAGE_PATH=storage_uri),
    )
    request = SimpleNamespace(scope={"router": router})
    session = UserSession(
        session_id="session",
        user_type=UserType.REGISTERED,
        user_id="user",
    )

    for candidate in (rn, legacy_rn):
        resolved = await resources.chatbot_content_by_rn(
            resources.RNContentRequest(rn=candidate),
            request=request,
            session=session,
        )

        assert resolved.content_type == "file"
        assert resolved.metadata["download_url"].endswith(
            "/attachment/turn_1/outputs/analysis/report.xlsx/download"
        )
