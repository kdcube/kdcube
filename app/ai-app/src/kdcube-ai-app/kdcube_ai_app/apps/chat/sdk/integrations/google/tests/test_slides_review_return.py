"""Live-return regressions: file threads, actionable errors and safe placement."""
import copy
import json

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_s3_proxy import Provider, call


def file_thread_provider():
    provider = Provider()
    # Reconstructed from the live observation: commentsViewMode includes a
    # Drive file comment as a native thread with no anchorId. Neutral values.
    provider.state["comments"].append({
        "commentId": "file_fixture", "status": "OPEN",
        "headPost": {"postId": "file_post", "content": "File review", "author": {"me": True}},
    })
    return provider


@pytest.mark.parametrize("operation,payload", [
    ("list_comments", {}),
    ("get_comment", {"comment_ref": "file_fixture"}),
    ("get_comment", {"comment_selector": {"text_contains": "File review"}}),
])
def test_file_thread_in_native_read_still_has_file_scope(monkeypatch, operation, payload):
    result, _ = call(monkeypatch, operation, payload, file_thread_provider())
    assert result["ok"], result
    row = next(row for row in result["ret"]["items"] if row["comment_ref"] == "file_fixture") if operation == "list_comments" else result["ret"]["comment"]
    assert row["scope"] == "file"
    assert "anchor_state" not in row and "post_ref" not in row


def test_file_thread_does_not_poison_a_slide_filtered_read(monkeypatch):
    result, _ = call(monkeypatch, "list_comments", {"slide": {"position": 1}}, file_thread_provider())
    assert result["ok"], result
    assert [row["comment_ref"] for row in result["ret"]["items"]] == ["anchored_fixture"]


@pytest.mark.parametrize("operation,method,suffix,body", [
    ("reply_comment", "POST", "/replies", {"content": "Changed"}),
    ("update_comment", "PATCH", "/file_fixture", {"content": "Changed"}),
    ("resolve_comment", "POST", "/replies", {"action": "resolve"}),
    ("delete_comment", "DELETE", "/file_fixture", {}),
])
def test_file_thread_present_in_native_read_mutates_through_drive(monkeypatch, operation, method, suffix, body):
    provider = file_thread_provider()
    # Native metadata cannot override the Drive head-author check for a file
    # thread. It is not a native mutation target even if it has a post ID.
    provider.state["comments"][-1]["headPost"]["author"] = {"me": False}
    result, provider = call(monkeypatch, operation, {"comment_ref": "file_fixture", "content": "Changed"}, provider)
    assert result["ok"], result
    assert len(provider.writes) == 1
    request, written = provider.writes[0]
    assert request.method == method
    assert request.url.path.startswith("/drive/v3/") and request.url.path.endswith(suffix)
    assert written == body
    assert result["ret"]["changes"]["scope"] == "file"
    assert "Drive comments have no atomic" in result["ret"]["revision_guard"]


@pytest.mark.parametrize("operation", ["reply_comment", "resolve_comment", "delete_comment", "update_comment"])
def test_an_unmapped_native_anchor_is_refused_instead_of_becoming_a_file_write(monkeypatch, operation):
    provider = Provider()
    provider.state["slides"][0]["commentAnchors"] = []
    result, provider = call(monkeypatch, operation, {"comment_ref": "anchored_fixture", "content": "Changed"}, provider)
    assert result["error"]["code"] == "slides_comment_anchors_unavailable"
    assert not provider.writes


def test_comment_400_keeps_sanitized_provider_evidence_and_adds_a_next_step(monkeypatch):
    provider = Provider()

    def failed(request):
        if request.method == "POST":
            return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "An error occurred while saving comments. Bearer fixture-secret"}})
        return provider(request)

    result, _ = call(monkeypatch, "resolve_comment", {"comment_ref": "anchored_fixture"}, failed)
    assert not result["ok"]
    assert result["error"]["message"] == "An error occurred while saving comments. Bearer [REDACTED]"
    assert result["ret"]["provider_status"] == 400
    assert result["ret"]["provider_code"] == "INVALID_ARGUMENT"
    assert result["ret"]["provider_reason"] == ""  # No reason was provided.
    assert "Drive" in result["ret"]["next_step"] and "Read the comments" in result["ret"]["next_step"]
    assert "fixture-secret" not in json.dumps(result)


def shape(ref, role, x, y, width, height):
    return {
        "objectId": ref,
        "size": {"width": {"magnitude": width, "unit": "PT"}, "height": {"magnitude": height, "unit": "PT"}},
        "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"},
        "shape": {"placeholder": {"type": role}, "text": {"textElements": [{"textRun": {"content": "Content\n"}}]}},
    }


@pytest.mark.parametrize("operation,extra", [
    ("add_table", {"values": [["Heading"], ["Value"]]}),
    ("add_video", {"video": "https://youtu.be/AbCdEf012_-"}),
    ("add_shape", {"text": "New content"}),
])
@pytest.mark.parametrize("preview", [False, True])
def test_relative_placement_below_a_full_height_body_is_refused_before_write(monkeypatch, operation, extra, preview):
    provider = Provider()
    provider.state["slides"][0]["pageElements"] = [shape("body", "BODY", 36, 90, 648, 315)]
    result, provider = call(monkeypatch, operation, {"slide": {"position": 1}, "below": {"role": "body"}, "preview": preview, **extra}, provider)
    assert result["error"]["code"] == "slides_placement_out_of_bounds"
    assert "grid cell" in result["error"]["message"]
    assert not provider.writes


@pytest.mark.parametrize("region", ["body", "missing_region"])
def test_a_missing_or_exhausted_body_never_falls_back_to_an_overlapping_video(monkeypatch, region):
    provider = Provider()
    provider.state["slides"][0]["pageElements"] = [
        shape("title", "TITLE", 36, 36, 648, 50),
        shape("subtitle", "SUBTITLE", 36, 110, 648, 280),
    ]
    result, provider = call(monkeypatch, "add_video", {"slide": {"position": 1}, "region": region, "video": "https://youtu.be/AbCdEf012_-"}, provider)
    assert result["error"]["code"] == "slides_region_missing"
    assert result["error"]["details"]["region"] == region
    assert not provider.writes


def test_synthetic_body_uses_free_space_below_the_subtitle(monkeypatch):
    provider = Provider()
    provider.state["slides"][0]["pageElements"] = [
        shape("title", "TITLE", 36, 36, 648, 50),
        shape("subtitle", "SUBTITLE", 36, 110, 648, 100),
    ]
    before = copy.deepcopy(provider.state)
    result, provider = call(monkeypatch, "add_video", {"slide": {"position": 1}, "region": "body", "video": "https://youtu.be/AbCdEf012_-"}, provider)
    assert result["ok"], result
    assert result["ret"]["changes"]["region"] == "body"
    props = provider.writes[0][1]["requests"][0]["createVideo"]["elementProperties"]
    assert props["transform"]["translateY"] > 210 * 12700
    assert not result["ret"]["diagnostics_summary"]["off_slide"]
    assert not result["ret"]["diagnostics_summary"]["overlaps"]
    assert before["slides"][0]["pageElements"][1]["transform"]["translateY"] == 110
