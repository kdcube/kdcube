"""S3 provider contracts; these exercise the public adapter entrypoint."""
import copy
import json
from pathlib import Path

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_proxy import run
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict

FIXTURES = Path(__file__).parent/"fixtures/slides/s3"


class Provider:
    def __init__(self):
        self.fixture = json.loads((FIXTURES/"comments.json").read_text())
        self.native = json.loads((FIXTURES/"native-comments.json").read_text())
        self.state = copy.deepcopy(self.fixture["presentation"])
        self.rows = copy.deepcopy(self.fixture["drive_comments"]["comments"])
        self.calls, self.writes = [], []
        self.pages = None
        self.save_state = "ALL_SAVED"
        self.omit_created_thread = False

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if request.method == "GET":
            if path.startswith("/v1/presentations/"):
                return httpx.Response(200, json=self.state)
            if path.endswith("/comments"):
                if self.pages is not None:
                    return httpx.Response(200, json=self.pages(request.url.params.get("pageToken", "")))
                return httpx.Response(200, json={"comments": self.rows})
            identity = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=next(row for row in self.rows if row["id"] == identity))
        body = json.loads(request.content) if request.content else {}
        self.writes.append((request, body))
        if path.endswith(":batchUpdate"):
            assert body["writeControl"] == {"requiredRevisionId": self.state["revisionId"]}
            self.state = predict(self.state, body["requests"])
            self.state["revisionId"] = "r2"
            for spec in body["requests"]:
                if "insertComment" in spec:
                    inserted = spec["insertComment"]
                    kind = "page" if inserted.get("objectId") == "page_fixture" else "element" if "objectId" in inserted else "shape_text" if "shapeTextAnchor" in inserted else "table_cell" if "tableCellTextAnchor" in inserted else "table_range"
                    response = copy.deepcopy(self.native["insert_responses"][kind])
                    thread = response["replies"][0]["insertComment"]["commentThread"]
                    thread["headPost"]["content"] = inserted["content"]
                    if not self.omit_created_thread:
                        self.state.setdefault("comments", []).append(copy.deepcopy(thread))
                    self.state["slides"][0].setdefault("commentAnchors", []).append(copy.deepcopy(self.native["anchors"][kind]))
                    response["commentUpdateState"] = self.save_state
                    return httpx.Response(200, json=response)
                if "deleteComment" in spec:
                    identity = spec["deleteComment"]["commentId"]
                    self.state["comments"] = [t for t in self.state["comments"] if t["commentId"] != identity]
                if "updateCommentPost" in spec:
                    update = spec["updateCommentPost"]
                    thread = next(t for t in self.state["comments"] if t["commentId"] == update["commentId"])
                    thread["headPost"]["content"] = update["content"]
                if "addCommentReply" in spec:
                    reply = spec["addCommentReply"]
                    thread = next(t for t in self.state["comments"] if t["commentId"] == reply["commentId"])
                    thread.setdefault("replies", []).append({"postId": "doco.reply", **reply["post"]})
                    if reply["post"].get("commentAction") == "RESOLVE":
                        thread["status"] = "RESOLVED"
            return httpx.Response(200, json={**self.fixture["saved"], "commentUpdateState": self.save_state})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "new_file_comment", "author": {"me": True}, **body})


def call(monkeypatch, operation, payload, provider=None):
    provider = provider or Provider()
    result = run(monkeypatch, operation, {"presentation_ref": "deck_fixture", **payload}, provider)
    return result, provider


@pytest.mark.parametrize("target,kind", [
    ({"slide": {"position": 1}}, "objectId"),
    ({"slide": {"position": 1}, "element": {"role": "title"}}, "objectId"),
    ({"quoted_text": "Roadmap"}, "shapeTextAnchor"),
    ({"slide": {"position": 1}, "table": {"ref": "table_fixture"}, "row": 1, "column": 1}, "tableCellTextAnchor"),
    ({"slide": {"position": 1}, "table": {"ref": "table_fixture"}, "table_range": {"from": {"row": 1, "column": 1}, "to": {"row": 2, "column": 2}}}, "tableAnchor"),
])
def test_each_anchor_is_written_as_a_direct_insert_comment_union(monkeypatch, target, kind):
    result, provider = call(monkeypatch, "create_comment", {"content": "Review this", **target})
    assert result["ok"], result
    assert len(provider.writes) == 1
    request, body = provider.writes[0]
    assert request.url.path.endswith(":batchUpdate")
    spec = body["requests"][0]["insertComment"]
    assert set(spec) == {"content", kind}
    assert result["ret"]["comment_ref"].startswith("doco.native_")
    assert result["ret"]["comment"]["anchor_state"] == "attached"
    assert result["ret"]["observed_revision_id"] == "r2"
    read = provider.calls[0]
    assert read.url.params["commentsViewMode"] == "COMMENTS_VIEW_MODE_INCLUDED"
    assert "commentAnchors" in read.url.params["fields"]


def test_unnamed_target_creates_a_drive_file_comment(monkeypatch):
    result, provider = call(monkeypatch, "create_comment", {"content": "Review the file"})
    assert result["ok"] and len(provider.writes) == 1
    request, body = provider.writes[0]
    assert request.url.path == "/drive/v3/files/deck_fixture/comments"
    assert body == {"content": "Review the file"}
    assert result["ret"]["comment"]["scope"] == "file"


@pytest.mark.parametrize("anchor", [
    {"row": 3, "column": 1},
    {"table_range": {"from": {"row": 3, "column": 1}, "to": {"row": 3, "column": 2}}},
])
def test_empty_table_comment_is_refused_without_a_provider_write(monkeypatch, anchor):
    result, provider = call(monkeypatch, "create_comment", {"content": "Comment", "slide": {"position": 1}, "table": {"ref": "table_fixture"}, **anchor})
    assert result["error"]["code"] == "slides_comment_target_empty"
    assert not provider.writes


def test_comment_page_joins_actual_anchor_locations(monkeypatch):
    result, provider = call(monkeypatch, "list_comments", {})
    assert result["ok"]
    rows = result["ret"]["items"]
    assert rows[0]["comment_ref"] == "anchored_fixture"
    assert rows[0]["targets"][0]["slide"]["ref"] == "page_fixture"
    assert rows[0]["targets"][0]["element"] == {"ref": "shape_fixture"}
    assert rows[1]["scope"] == "file"
    assert result["ret"]["next_cursor"] is None
    assert "fixture-token" not in json.dumps(result)


def test_filtered_empty_page_retains_the_provider_cursor(monkeypatch):
    provider = Provider()
    provider.pages = lambda cursor: {"comments": [{"id": "resolved_fixture", "resolved": True}], "nextPageToken": "next_page"}
    result, _ = call(monkeypatch, "list_comments", {"cursor": "first_page"}, provider)
    assert result["ret"]["items"] == []
    assert result["ret"]["next_cursor"] == "next_page"
    assert provider.calls[1].url.params["pageToken"] == "first_page"


@pytest.mark.parametrize("operation", ["update_comment", "delete_comment"])
@pytest.mark.parametrize("author", [{"me": False}, {}])
def test_other_or_unknown_author_is_refused_before_any_mutation(monkeypatch, operation, author):
    provider = Provider()
    provider.rows[2]["author"] = author
    result, provider = call(monkeypatch, operation, {"comment_ref": "foreign_fixture", "content": "New text"}, provider)
    assert result["error"]["code"] == "slides_comment_not_author"
    assert not provider.writes


@pytest.mark.parametrize("operation,request_kind", [("reply_comment", "addCommentReply"), ("update_comment", "updateCommentPost"), ("delete_comment", "deleteComment")])
def test_anchored_thread_mutation_uses_slides_and_real_head_post_id(monkeypatch, operation, request_kind):
    result, provider = call(monkeypatch, operation, {"comment_ref": "anchored_fixture", "content": "Changed"})
    assert result["ok"], result
    spec = provider.writes[0][1]["requests"][0][request_kind]
    assert spec["commentId"] == "anchored_fixture"
    if operation == "update_comment":
        assert spec["postId"] == "post_fixture" and spec["postId"] != spec["commentId"]
    assert len(provider.writes) == 1


@pytest.mark.parametrize("operation,method,suffix", [("reply_comment", "POST", "/replies"), ("update_comment", "PATCH", "/file_fixture"), ("delete_comment", "DELETE", "/file_fixture")])
def test_file_thread_mutation_uses_drive(monkeypatch, operation, method, suffix):
    result, provider = call(monkeypatch, operation, {"comment_ref": "file_fixture", "content": "Changed"})
    assert result["ok"], result
    request, body = provider.writes[0]
    assert request.method == method and request.url.path.endswith(suffix)
    assert ":batchUpdate" not in request.url.path


def test_file_resolution_uses_drive_reply_action_resolve(monkeypatch):
    result, provider = call(monkeypatch, "resolve_comment", {"comment_ref": "file_fixture"})
    assert result["ok"]
    request, body = provider.writes[0]
    assert request.url.path.endswith("/file_fixture/replies")
    assert body == {"action": "resolve"}


def test_native_resolution_puts_comment_action_inside_post_and_reads_status(monkeypatch):
    result, provider = call(monkeypatch, "resolve_comment", {"comment_ref": "anchored_fixture"})
    assert result["ok"], result
    post = provider.writes[0][1]["requests"][0]["addCommentReply"]["post"]
    assert post == {"content": "Resolved.", "commentAction": "RESOLVE"}
    assert result["ret"]["comment"]["resolved"]
    assert "revision_guard" not in result["ret"]


@pytest.mark.parametrize("state", ["ALL_FAILED_UNKNOWN_REASON", "NO_UPDATES_REQUESTED", None])
def test_unconfirmed_comment_save_never_reports_success_or_retries(monkeypatch, state):
    provider = Provider()
    provider.save_state = state
    result, provider = call(monkeypatch, "create_comment", {"content": "Review", "slide": {"position": 1}}, provider)
    assert result["error"]["code"] == "slides_comment_not_saved"
    assert result["ret"]["outcome_unknown"] and result["ret"]["partial_result"]
    assert len(provider.writes) == 1


def test_comment_selector_refuses_an_incomplete_scan(monkeypatch):
    provider = Provider()
    provider.pages = lambda cursor: {"comments": [], "nextPageToken": str(int(cursor or 0)+1)}
    result, provider = call(monkeypatch, "get_comment", {"comment_selector": {"author": "me"}}, provider)
    assert result["error"]["code"] == "slides_comment_selection_incomplete"
    assert len(provider.calls) == 11 and not provider.writes


def test_comment_selector_checks_every_page_before_deciding_uniqueness(monkeypatch):
    provider = Provider()
    provider.pages = lambda cursor: {"comments": [{"id": "first" if not cursor else "second", "content": "Review", "author": {"me": True}}], **({"nextPageToken": "next"} if not cursor else {})}
    result, provider = call(monkeypatch, "reply_comment", {"comment_selector": {"text_contains": "Review"}, "content": "Reply"}, provider)
    assert result["error"]["code"] == "slides_comment_ambiguous"
    assert len(result["error"]["details"]["candidates"]) == 2
    assert not provider.writes


@pytest.mark.parametrize("operation", ["create_comment", "update_comment", "reply_comment", "delete_comment", "resolve_comment"])
def test_comment_previews_write_nothing(monkeypatch, operation):
    payload = {"preview": True, "content": "Review"}
    if operation != "create_comment":
        payload["comment_ref"] = "anchored_fixture"
    result, provider = call(monkeypatch, operation, payload)
    assert result["ok"] and result["ret"]["preview"]
    assert not provider.writes


def test_stale_comment_revision_is_refused_before_thread_read_or_write(monkeypatch):
    result, provider = call(monkeypatch, "update_comment", {"comment_ref": "anchored_fixture", "content": "Changed", "revision_id": "old_revision"})
    assert result["error"]["code"] == "slides_revision_changed"
    assert len(provider.calls) == 1 and not provider.writes


@pytest.mark.parametrize("operation,payload", [
    ("add_table", {"values": [["Heading"], ["Value"]]}),
    ("add_video", {"video": "https://youtu.be/AbCdEf012_-"}),
    ("set_cells", {"table": {"ref": "table_fixture"}, "cells": [{"row": 2, "column": 1, "text": "Done"}]}),
    ("add_row", {"table": {"ref": "table_fixture"}, "values": ["Next", "Editor"]}),
])
def test_s3_content_writes_are_revision_guarded_and_read_back(monkeypatch, operation, payload):
    result, provider = call(monkeypatch, operation, {"slide": {"position": 1}, **payload})
    assert result["ok"], result
    assert len(provider.writes) == 1
    assert result["ret"]["revision_id"] == "r2"
    assert result["ret"]["observed_revision_id"] == "r2"
    assert "diagnostics_summary" in result["ret"]


def test_anchor_mapping_failure_refuses_slide_filter_instead_of_hiding_threads(monkeypatch):
    provider = Provider()
    provider.state["slides"][0]["commentAnchors"] = []
    result, _ = call(monkeypatch, "list_comments", {"slide": {"position": 1}}, provider)
    assert result["error"]["code"] == "slides_comment_anchors_unavailable"


def test_slide_scope_cannot_mutate_a_file_comment(monkeypatch):
    result, provider = call(monkeypatch, "delete_comment", {"slide": {"position": 1}, "comment_ref": "file_fixture"})
    assert result["error"]["code"] == "slides_comment_target_required"
    assert not provider.writes


def test_observed_drive_json_anchors_map_pages_and_elements_without_inventing_cells(monkeypatch):
    provider = Provider()
    provider.rows = json.loads((FIXTURES/"drive-ui-anchors.json").read_text())["comments"]
    provider.state["comments"] = []
    provider.state["slides"][0]["commentAnchors"] = []
    result, _ = call(monkeypatch, "list_comments", {"slide": {"position": 1}}, provider)
    assert result["ok"], result
    rows = result["ret"]["items"]
    assert len(rows) == 3
    assert rows[0]["targets"][0]["slide"]["ref"] == "page_fixture"
    assert rows[1]["targets"][0]["element"] == {"ref": "shape_fixture"}
    target = rows[2]["targets"][0]
    assert target["element"] == {"ref": "table_fixture"}
    assert target["cell_location"] == "unavailable_in_provider_anchor"
    assert "row" not in target and "column" not in target
    assert rows[2]["quoted_text"] == "Ready"
    assert rows[2]["replies"][0]["reply_ref"].startswith("doco.")
    assert not any(row["resolved"] for row in rows)  # Missing resolved is open.


def test_guessed_or_detached_drive_anchor_does_not_count_as_mapped(monkeypatch):
    provider = Provider()
    provider.state["comments"] = []
    provider.rows = [{"id": "doco.unknown", "anchor": json.dumps({"r": "head", "a": [{"page": {"p": "page_fixture"}}]})}]
    result, _ = call(monkeypatch, "list_comments", {}, provider)
    assert result["ok"]
    assert result["ret"]["items"][0]["anchor_state"] == "unavailable"
    assert "targets" not in result["ret"]["items"][0]


def test_native_empty_cell_location_is_the_first_cell_and_range_collapse_is_reported(monkeypatch):
    result, _ = call(monkeypatch, "create_comment", {"content": "Review", "slide": {"position": 1}, "table": {"ref": "table_fixture"}, "table_range": {"from": {"row": 1, "column": 1}, "to": {"row": 2, "column": 2}}})
    assert result["ok"], result
    target = result["ret"]["comment"]["targets"][0]
    assert target["row"] == 1 and target["column"] == 1
    assert target["location_precision"] == "cell"
    assert result["ret"]["changes"]["requested_target"]["scope"] == "table_range"
    assert result["ret"]["changes"]["saved_targets"] == [target]
    assert result["ret"]["anchor_warning"]


def test_confirmed_write_without_readback_does_not_claim_an_observed_anchor(monkeypatch):
    provider = Provider()
    provider.omit_created_thread = True
    result, provider = call(monkeypatch, "create_comment", {"content": "Review", "slide": {"position": 1}}, provider)
    assert result["error"]["code"] == "slides_comment_readback_unavailable"
    assert result["ret"]["save_confirmed"] and result["ret"]["partial_result"]
    assert result["ret"]["created_comment_ref"] == "doco.native_page"
    assert len(provider.writes) == 1


def test_absent_native_anchor_collection_does_not_break_file_comment_reads(monkeypatch):
    provider = Provider()
    provider.state["comments"] = None
    provider.state["slides"][0]["commentAnchors"] = None
    result, _ = call(monkeypatch, "list_comments", {}, provider)
    assert result["ok"], result
