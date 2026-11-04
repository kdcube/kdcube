"""Drive comment paging joined to Slides anchors; guarded comment mutations."""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

from kdcube_ai_app.apps.chat.sdk.integrations.docs.selectors import matching_comments
from kdcube_ai_app.apps.chat.sdk.integrations.slides.comments import anchor_views, comment_content, compile_anchor, drive_anchor_view
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_slide
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tables import shared_selector

DRIVE_API = "https://www.googleapis.com/drive/v3"
COMMENT_FIELDS = "id,content,anchor,resolved,createdTime,modifiedTime,author(displayName,me),quotedFileContent(value),replies(id,content,action,createdTime,author(displayName,me))"
MAX_PAGES = 10
MAX_PAGE = 100


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise SlidesValidationError("slides_comment_target_required", "Choose comment_ref from list_comments or use a comment_selector.")
    return quote(value, safe="")


def _row(raw, thread, anchors, presentation=None):
    author = raw.get("author") or {}
    head = (thread or {}).get("headPost") or {}
    row = {
        "comment_ref": raw["id"], "comment_id": raw["id"],
        "content": str(raw.get("content", ""))[:12000],
        "content_truncated": len(str(raw.get("content", ""))) > 12000,
        "resolved": bool(raw.get("resolved")), "author": author.get("displayName", ""),
        "author_is_me": author.get("me") is True,
        "created_time": raw.get("createdTime"), "modified_time": raw.get("modifiedTime"),
        "quoted_text": str((raw.get("quotedFileContent") or {}).get("value", (thread or {}).get("plainTextQuote", "")))[:12000],
        "replies": [{"reply_ref": r.get("id"), "content": str(r.get("content", ""))[:12000], "action": r.get("action"), "author": (r.get("author") or {}).get("displayName", ""), "author_is_me": (r.get("author") or {}).get("me") is True} for r in raw.get("replies", [])[:100]],
        "replies_truncated": len(raw.get("replies", [])) > 100,
    }
    if thread:
        view = anchors.get(thread.get("anchorId"), {"scope": "anchored", "anchor_state": "unavailable"})
        if view.get("anchor_state") != "attached" and raw.get("anchor"):
            view = drive_anchor_view(presentation or {}, raw)
        row.update(view)
        row["post_ref"] = head.get("postId")
    else:
        # An unknown anchored thread must never silently become a file comment.
        row.update(drive_anchor_view(presentation or {}, raw))
    return row


def _native_row(thread, presentation):
    head = thread.get("headPost", {})
    raw = {"id": thread["commentId"], "content": head.get("content", ""), "author": head.get("author", {}), "createdTime": head.get("createTime"), "modifiedTime": head.get("updateTime"), "resolved": thread.get("status") == "RESOLVED", "quotedFileContent": {"value": thread.get("plainTextQuote", "")}, "replies": [{"id": p.get("postId"), "content": p.get("content", ""), "author": p.get("author", {}), "createdTime": p.get("createTime"), "action": p.get("commentAction")} for p in thread.get("replies", [])]}
    return _row(raw, thread, anchor_views(presentation), presentation)


async def _page(api, deck_id, cursor, limit):
    return await api.request("GET", f"{DRIVE_API}/files/{deck_id}/comments", operation="list_comments", params={"pageSize": limit, "pageToken": cursor, "includeDeleted": "false", "fields": f"nextPageToken,comments({COMMENT_FIELDS})"})


async def _select(api, deck_id, payload, presentation):
    identity = payload.get("comment_ref")
    selector = payload.get("comment_selector")
    if identity and selector is not None:
        raise SlidesValidationError("slides_comment_selector_invalid", "Choose comment_ref or comment_selector, with one selection form.")
    if identity:
        raw = await api.request("GET", f"{DRIVE_API}/files/{deck_id}/comments/{_identity(identity)}", operation="get_comment", params={"fields": COMMENT_FIELDS, "includeDeleted": "false"})
        if raw.get("id") != identity:
            raise SlidesValidationError("slides_comment_not_found", "Google returned no matching comment. List the comments again.")
        return raw
    if not isinstance(selector, (str, Mapping)) or not selector:
        raise SlidesValidationError("slides_comment_target_required", "Pass comment_ref or a lexical comment_selector from list_comments.")
    if isinstance(selector, Mapping):
        selector = dict(selector)
        if "quoted_text" in selector:
            selector["quoted_text_contains"] = selector.pop("quoted_text")
        if "unresolved" in selector:
            if type(selector["unresolved"]) is not bool:
                raise SlidesValidationError("slides_comment_selector_invalid", "Pass unresolved as true or false.")
            selector["resolved"] = not selector.pop("unresolved")
        allowed = {"text_contains", "quoted_text_contains", "author", "author_contains", "position", "resolved"}
        if set(selector)-allowed:
            raise SlidesValidationError("slides_comment_selector_invalid", "Use text_contains, quoted_text, author, unresolved or a returned comment_ref.")
    cursor, raws = "", []
    seen = set()
    for _ in range(MAX_PAGES):
        page = await _page(api, deck_id, cursor, MAX_PAGE)
        raws.extend(page.get("comments", []))
        cursor = page.get("nextPageToken")
        if not cursor:
            break
        if cursor in seen:
            raise SlidesValidationError("slides_comment_selection_incomplete", "Google repeated a comment cursor. Use a returned comment_ref after listing the relevant page.")
        seen.add(cursor)
    if cursor:
        raise SlidesValidationError("slides_comment_selection_incomplete", "This presentation has more than 1000 comments. Page through list_comments and choose comment_ref; a partial scan cannot select a unique comment.")
    threads = {t["commentId"]: t for t in presentation.get("comments") or []}
    anchors = anchor_views(presentation)
    rows = [_row(raw, threads.get(raw["id"]), anchors, presentation) for raw in raws]
    matches = shared_selector(matching_comments, rows, selector)
    if len(matches) != 1:
        raise SlidesValidationError("slides_comment_ambiguous" if matches else "slides_comment_not_found", "Choose one returned comment_ref or narrow the comment selector.", details={"candidates": matches[:5], "candidates_truncated": len(matches) > 5})
    return next(raw for raw in raws if raw["id"] == matches[0]["comment_id"])


async def run_comments(api, operation, payload, deck_id, presentation):
    threads = {t["commentId"]: t for t in presentation.get("comments") or []}
    anchors = anchor_views(presentation)
    base = {"presentation_id": deck_id, "title": presentation.get("title", ""), "revision_id": presentation.get("revisionId")}
    if operation == "list_comments":
        limit = payload.get("limit", 50)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise SlidesValidationError("slides_invalid_limit", "Use a comment limit from 1 to 100.")
        page = await _page(api, deck_id, str(payload.get("cursor") or ""), limit)
        rows = [_row(raw, threads.get(raw["id"]), anchors, presentation) for raw in page.get("comments", [])]
        if not payload.get("include_resolved"):
            rows = [r for r in rows if not r["resolved"]]
        if "slide" in payload:
            slide = resolve_slide((await api.all_snapshot(presentation))["slides"], payload["slide"])
            if any(r["scope"] == "anchored" and r["anchor_state"] != "attached" for r in rows):
                raise SlidesValidationError("slides_comment_anchors_unavailable", "Some comment anchors could not be mapped. List without a slide filter to retain those comments.")
            rows = [r for r in rows if any(t["slide"]["ref"] == slide["ref"] for t in r.get("targets", []))]
        return {**base, "items": rows, "next_cursor": page.get("nextPageToken")}
    if operation == "create_comment":
        text = comment_content(payload.get("content"))
        anchor, target = compile_anchor(presentation, await api.all_snapshot(presentation), payload)
        if payload.get("preview"):
            return {**base, "preview": True, "changes": {**target, "content": text}, **({"anchor_warning": "Google may normalize a table range to one cell text anchor. The saved target is read back after writing."} if target["scope"] == "table_range" else {})}
        if anchor is None:
            result = await api.request("POST", f"{DRIVE_API}/files/{deck_id}/comments", operation=operation, mutating=True, params={"fields": COMMENT_FIELDS}, json={"content": text})
            if not result.get("id"):
                raise SlidesValidationError("slides_comment_not_saved", "Google returned no new comment identity. Read the comments before retrying.", details={"outcome_unknown": True})
            return {**base, "comment_ref": result["id"], "comment": _row(result, None, {}), "changes": target, "_drive_write": True}
        written = await api.write_batches(deck_id, [{"insertComment": {"content": text, **anchor}}], presentation.get("revisionId"), operation=operation)
        inserted = next((r.get("insertComment", {}).get("commentThread") for r in written["replies"] if r.get("insertComment", {}).get("commentThread")), {})
        identity = inserted.get("commentId")
        if not identity:
            raise SlidesValidationError("slides_comment_readback_unavailable", "Google confirmed the save but returned no new thread identity. List comments before retrying.", details={"save_confirmed": True, "outcome_unknown": True})
        api.created_comment_id = identity
        observed = await api.get(deck_id, include_comments=True)
        saved = next((t for t in observed.get("comments") or [] if t.get("commentId") == identity), None)
        if saved is None:
            raise SlidesValidationError("slides_comment_readback_unavailable", "Google confirmed the save but the new thread is absent from the read-back. Read it again before retrying.", details={"comment_ref": identity, "save_confirmed": True, "outcome_unknown": False})
        comment = _native_row(saved, observed)
        result = {**base, **written, "observed_revision_id": observed.get("revisionId"), "comment_ref": identity, "comment": comment, "changes": {"requested_target": target, "saved_targets": comment.get("targets", [])}}
        if target["scope"] == "table_range":
            result["anchor_warning"] = "The requested table range is provider-normalized; the read-back targets describe the saved anchor and do not promise a durable range."
        return result
    raw = await _select(api, deck_id, payload, presentation)
    identity = raw["id"]
    thread = threads.get(identity)
    row = _row(raw, thread, anchors, presentation)
    if "slide" in payload:
        slide = resolve_slide((await api.all_snapshot(presentation))["slides"], payload["slide"])
        if not any(t["slide"]["ref"] == slide["ref"] for t in row.get("targets", [])):
            raise SlidesValidationError("slides_comment_target_required", "This comment is not mapped to the selected slide. Address the presentation to act on a file comment or an unavailable anchor.")
    if operation == "get_comment":
        return {**base, "comment": row}
    if operation in ("update_comment", "delete_comment"):
        author = (thread.get("headPost") or {}).get("author", {}) if thread else raw.get("author", {})
        if author.get("me") is not True:
            raise SlidesValidationError("slides_comment_not_author", "Only the head post's author may edit or delete this comment. Reply instead.")
    text = comment_content(payload.get("content")) if operation in ("update_comment", "reply_comment") else comment_content(payload.get("content", "Resolved.")) if operation == "resolve_comment" and thread else None
    if row["scope"] == "anchored" and not thread:
        raise SlidesValidationError("slides_comment_anchors_unavailable", "The anchored comment is absent from the Slides read. Read it again before editing.")
    if payload.get("preview"):
        return {**base, "preview": True, "comment_ref": identity, "changes": {"action": operation, **({"content": text} if text is not None else {})}}
    url = f"{DRIVE_API}/files/{deck_id}/comments/{_identity(identity)}"
    if thread:
        if operation == "resolve_comment":
            request = {"addCommentReply": {"commentId": identity, "post": {"content": text, "commentAction": "RESOLVE"}}}
        elif operation == "reply_comment":
            request = {"addCommentReply": {"commentId": identity, "post": {"content": text}}}
        elif operation == "update_comment":
            post = thread.get("headPost", {}).get("postId")
            if not post:
                raise SlidesValidationError("slides_comment_target_required", "Google returned no head post ref. Read the comment again.")
            request = {"updateCommentPost": {"commentId": identity, "postId": post, "content": text}}
        else:
            request = {"deleteComment": {"commentId": identity}}
        written = await api.write_batches(deck_id, [request], presentation.get("revisionId"), operation=operation)
        base.update(revision_id=written["revision_id"])
        observed = await api.get(deck_id, include_comments=True)
        saved = next((t for t in observed.get("comments") or [] if t.get("commentId") == identity), None)
        base["observed_revision_id"] = observed.get("revisionId")
        if operation == "delete_comment":
            if saved is not None:
                raise SlidesValidationError("slides_comment_readback_unavailable", "Google confirmed deletion but the thread remains in the read-back. Read again before retrying.", details={"comment_ref": identity, "save_confirmed": True})
            base["deleted"] = True
        elif saved is None:
            raise SlidesValidationError("slides_comment_readback_unavailable", "The saved thread is absent from the read-back. Read comments before retrying.", details={"comment_ref": identity, "save_confirmed": True})
        else:
            base["comment"] = _native_row(saved, observed)
    elif operation == "resolve_comment":
        await api.request("POST", f"{url}/replies", operation=operation, mutating=True, params={"fields": "id,action"}, json={"action": "resolve"})
    elif operation == "reply_comment":
        await api.request("POST", f"{url}/replies", operation=operation, mutating=True, params={"fields": "id,content"}, json={"content": text})
    elif operation == "update_comment":
        await api.request("PATCH", url, operation=operation, mutating=True, params={"fields": "id,content"}, json={"content": text})
    else:
        await api.request("DELETE", url, operation=operation, mutating=True)
    return {**base, "comment_ref": identity, "changes": {"action": operation, "scope": row["scope"]}, "_drive_write": not bool(thread)}
