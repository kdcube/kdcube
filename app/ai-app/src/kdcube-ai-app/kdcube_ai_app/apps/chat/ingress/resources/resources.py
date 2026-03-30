# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/ingress/resources/resources.py
from typing import List, Dict, Any, Optional
import logging
import json, io, mimetypes, re
from urllib.parse import unquote, quote

from pydantic import BaseModel
from fastapi import Depends, HTTPException, APIRouter, Request
from fastapi.responses import StreamingResponse

from kdcube_ai_app.apps.chat.ingress.resolvers import require_auth, get_project, get_tenant_dep
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.auth.AuthManager import PRIVILEGED_ROLES, RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.tools.content_type import get_mime_type_enhanced

"""
Resources API

File: api/resources/resources.py
"""


logger = logging.getLogger("Resources.API")

# Create router
router = APIRouter()

class RNContentRequest(BaseModel):
    rn: str

class RNContentResponse(BaseModel):
    rn: str
    content_type: str
    content: Any
    metadata: Dict[str, Any]

def _ok_headers(filename: str, mime: str, as_attachment: bool) -> dict:
    disp = "attachment" if as_attachment else "inline"
    def _ascii_fallback(name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "").strip("._")
        return safe or "file"
    def _content_disposition(name: str) -> str:
        try:
            name.encode("latin-1")
            return f'{disp}; filename="{name}"'
        except Exception:
            ascii_name = _ascii_fallback(name)
            encoded = quote(name or "", safe="")
            return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
    return {
        "Content-Disposition": _content_disposition(filename),
        "Content-Type": mime,
    }

def _is_text_mime(mt: str) -> bool:
    return (mt or "").startswith("text/") or mt in ("application/json","application/xml")

def _parse_chatbot_rn(rn: str) -> Dict[str, Any]:
    # ef:<tenant>:<project>:chatbot:<stage>:<user_id>:<conversation_id>:<turn_id>:<role>:<tail...>
    parts = rn.split(":")
    if len(parts) < 9 or parts[0] != "ef" or parts[3] != "chatbot":
        raise HTTPException(status_code=400, detail="Invalid RN format")
    return {
        "tenant": parts[1],
        "project": parts[2],
        "stage": parts[4],
        "user_id": parts[5].replace("%3A", ":"),
        "conversation_id": parts[6],
        "turn_id": parts[7],
        "role": parts[8],
        "tail": parts[9:] if len(parts) > 9 else []
    }

def _is_owner_or_privileged(owner_id: str, session: UserSession) -> bool:
    if session.user_id and owner_id == session.user_id:
        return True
    if (session.user_type.value or "").lower() == "anonymous" and owner_id and session.fingerprint and owner_id == session.fingerprint:
        return True
    if PRIVILEGED_ROLES & set(session.roles or []):
        return True
    return False

def _pick_existing_rel(store: ConversationStore, rels: List[str]) -> Optional[str]:
    for rel in rels:
        if store.backend.exists(rel):
            return rel
    return None

@router.post("/by-rn", response_model=RNContentResponse)
async def chatbot_content_by_rn(req: RNContentRequest,
                                request: Request,
                                session: UserSession = Depends(require_auth(RequireUser()))):
    p = _parse_chatbot_rn(req.rn)
    tenant, project, stage = p["tenant"], p["project"], p["stage"]
    owner_id, conv_id, turn_id, role = p["user_id"], p["conversation_id"], p["turn_id"], p["role"]
    tail: List[str] = p["tail"]

    if not _is_owner_or_privileged(owner_id, session):
        raise HTTPException(status_code=403, detail="Forbidden")

    store = ConversationStore(get_settings().STORAGE_PATH)

    def conv_rel(uid: str, message_id: str) -> str:
        return f"cb/tenants/{tenant}/projects/{project}/conversation/{uid}/{conv_id}/{turn_id}/{message_id}.json"

    def conv_rel_legacy(who: str, uid: str, message_id: str) -> str:
        return f"cb/tenants/{tenant}/projects/{project}/conversation/{who}/{uid}/{conv_id}/{turn_id}/{message_id}.json"

    def att_rel(uid: str, filename: str) -> str:
        return f"cb/tenants/{tenant}/projects/{project}/attachments/{uid}/{conv_id}/{turn_id}/{filename}"

    def att_rel_legacy(who: str, uid: str, filename: str) -> str:
        return f"cb/tenants/{tenant}/projects/{project}/attachments/{who}/{uid}/{conv_id}/{turn_id}/{filename}"

    def exec_rel(uid: str, kind: str, path: str) -> str:
        return f"cb/tenants/{tenant}/projects/{project}/executions/{uid}/{conv_id}/{turn_id}/{kind}/{path}"

    def exec_rel_legacy(who: str, uid: str, kind: str, path: str) -> str:
        return f"cb/tenants/{tenant}/projects/{project}/executions/{who}/{uid}/{conv_id}/{turn_id}/{kind}/{path}"

    try:
        # ----- message -----
        if stage == "message":
            if len(tail) < 1:
                raise HTTPException(status_code=400, detail="Missing message_id")
            message_id = tail[0]
            legacy = [conv_rel_legacy(w, owner_id, message_id) for w in ("registered", "anonymous", "privileged", "paid")]
            rel = _pick_existing_rel(store, [conv_rel(owner_id, message_id)] + legacy)
            if not rel:
                raise HTTPException(status_code=404, detail="Not found")
            raw = store.backend.read_text(rel)
            obj = json.loads(raw)
            return RNContentResponse(rn=req.rn, content_type="message", content=obj,
                                     metadata={"uri": store._uri_for_path(rel)})

        router_or_app = request.scope.get("router") or request.scope.get("app")
        # ----- attachment/file -----
        if stage in ("file", "attachment"):
            if len(tail) < 1:
                raise HTTPException(status_code=400, detail="Missing filename")
            filename = tail[0].replace("%3A", ":")
            legacy = [att_rel_legacy(w, owner_id, filename) for w in ("registered", "anonymous", "privileged", "paid")]
            rel = _pick_existing_rel(store, [att_rel(owner_id, filename)] + legacy)
            if not rel:
                raise HTTPException(status_code=404, detail="Not found")
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            uri = store._uri_for_path(rel)

            meta = {
                "mime": mime, "uri": uri,
                "preview_url": str(router_or_app.url_path_for(
                    "preview_cb_attachment",
                    tenant=tenant, project=project,
                    owner_id=owner_id, conversation_id=conv_id,
                    turn_id=turn_id, filename=filename
                )),
                "download_url": str(router_or_app.url_path_for(
                    "download_cb_attachment",
                    tenant=tenant, project=project,
                    owner_id=owner_id, conversation_id=conv_id,
                    turn_id=turn_id, filename=filename
                ))
            }
            if _is_text_mime(mime):
                content = store.backend.read_text(rel)
            else:
                content = f"[Binary content: {mime}] Use preview_url or download_url."
                meta["is_binary"] = True
            return RNContentResponse(rn=req.rn, content_type="file", content=content, metadata=meta)

        # ----- execution -----
        if stage == "execution":
            if len(tail) < 2:
                raise HTTPException(status_code=400, detail="Execution RN must be :execution:<kind>:<rel_path>")
            kind = tail[0]
            rel_path = "/".join([seg.replace("%3A", ":") for seg in tail[1:]])
            legacy = [exec_rel_legacy(w, owner_id, kind, rel_path) for w in ("registered", "anonymous", "privileged", "paid")]
            rel = _pick_existing_rel(store, [exec_rel(owner_id, kind, rel_path)] + legacy)
            if not rel:
                raise HTTPException(status_code=404, detail="Not found")
            mime = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
            uri = store._uri_for_path(rel)
            meta = {
                "mime": mime, "uri": uri,
                "preview_url": router_or_app.url_path_for(
                    "preview_cb_exec_file",
                    tenant=tenant, project=project,
                    owner_id=owner_id, conversation_id=conv_id,
                    turn_id=turn_id, kind=kind, path=rel_path
                ),
                "download_url": router_or_app.url_path_for(
                    "download_cb_exec_file",
                    tenant=tenant, project=project,
                    owner_id=owner_id, conversation_id=conv_id,
                    turn_id=turn_id, kind=kind, path=rel_path
                )
            }
            if _is_text_mime(mime):
                content = store.backend.read_text(rel)
            else:
                content = f"[Binary content: {mime}] Use preview_url or download_url."
                meta["is_binary"] = True
            return RNContentResponse(rn=req.rn, content_type="execution", content=content, metadata=meta)

        # ----- citable -----
        if stage == "citable":
            if len(tail) < 1:
                raise HTTPException(status_code=400, detail="Missing message_id")
            message_id = tail[0]
            pick = _pick_namespace_exists(store, tenant, project, owner_id, conv_id, turn_id,
                                          lambda who, uid: conv_rel(who, uid, message_id))
            if not pick:
                raise HTTPException(status_code=404, detail="Not found")
            who, uid = pick
            rel = conv_rel(who, uid, message_id)
            obj = json.loads(store.backend.read_text(rel))
            return RNContentResponse(rn=req.rn, content_type="citable", content=obj,
                                     metadata={"uri": store._uri_for_path(rel)})

        raise HTTPException(status_code=400, detail=f"Unsupported stage: {stage}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("RN resolve error")
        raise HTTPException(status_code=500, detail=str(e))


# --------------------- ATTACHMENTS ---------------------

@router.get("/{tenant}/{project}/conv/{owner_id}/{conversation_id}/turn/{turn_id}/attachment/{filename}/preview")
async def preview_cb_attachment(
    tenant: str = Depends(get_tenant_dep),
    project: str = Depends(get_project),
    owner_id: str = "",
    conversation_id: str = "",
    turn_id: str = "",
    filename: str = "",
    attached: Optional[bool] = False,
    session: UserSession = Depends(require_auth(RequireUser())),
):
    try:
        if not _is_owner_or_privileged(owner_id, session):
            raise HTTPException(status_code=403, detail="Forbidden")

        store = ConversationStore(get_settings().STORAGE_PATH)
        filename = unquote(filename)

        # try both namespaces with provided owner_id
        rels = [
            f"cb/tenants/{tenant}/projects/{project}/attachments/{owner_id}/{conversation_id}/{turn_id}/{filename}"
        ] + [
            f"cb/tenants/{tenant}/projects/{project}/attachments/{who}/{owner_id}/{conversation_id}/{turn_id}/{filename}"
            for who in ("registered", "anonymous", "privileged", "paid")
        ]
        rel = _pick_existing_rel(store, rels)
        if not rel:
            raise HTTPException(status_code=404, detail="Attachment not found")

        raw = store.backend.read_bytes(rel)
        mime = get_mime_type_enhanced(filename, raw) or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
        headers = _ok_headers(filename, mime, as_attachment=bool(attached))

        return StreamingResponse(io.BytesIO(raw), media_type=mime, headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("preview_cb_attachment error")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{tenant}/{project}/conv/{owner_id}/{conversation_id}/turn/{turn_id}/attachment/{filename}/download")
async def download_cb_attachment(
    tenant: str = Depends(get_tenant_dep),
    project: str = Depends(get_project),
    owner_id: str = "",
    conversation_id: str = "",
    turn_id: str = "",
    filename: str = "",
    session: UserSession = Depends(require_auth(RequireUser())),
):
    return await preview_cb_attachment(tenant, project, owner_id, conversation_id, turn_id, filename, True, session)

# --------------------- EXECUTION FILES ---------------------

@router.get("/{tenant}/{project}/conv/{owner_id}/{conversation_id}/turn/{turn_id}/exec/{kind}/{path:path}/preview")
async def preview_cb_exec_file(
    tenant: str = Depends(get_tenant_dep),
    project: str = Depends(get_project),
    owner_id: str = "",
    conversation_id: str = "",
    turn_id: str = "",
    kind: str = "",
    path: str = "",
    attached: Optional[bool] = False,
    session: UserSession = Depends(require_auth(RequireUser())),
):
    try:
        if not _is_owner_or_privileged(owner_id, session):
            raise HTTPException(status_code=403, detail="Forbidden")

        store = ConversationStore(get_settings().STORAGE_PATH)
        rel_path = unquote(path)

        rels = [
            f"cb/tenants/{tenant}/projects/{project}/executions/{owner_id}/{conversation_id}/{turn_id}/{kind}/{rel_path}"
        ] + [
            f"cb/tenants/{tenant}/projects/{project}/executions/{who}/{owner_id}/{conversation_id}/{turn_id}/{kind}/{rel_path}"
            for who in ("registered", "anonymous", "privileged", "paid")
        ]
        rel = _pick_existing_rel(store, rels)
        if not rel:
            raise HTTPException(status_code=404, detail="Execution file not found")

        raw = store.backend.read_bytes(rel)
        filename = rel_path.split("/")[-1] or kind
        mime = get_mime_type_enhanced(filename, raw) or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
        headers = _ok_headers(filename, mime, as_attachment=bool(attached))
        return StreamingResponse(io.BytesIO(raw), media_type=mime, headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("preview_cb_exec_file error")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{tenant}/{project}/conv/{owner_id}/{conversation_id}/turn/{turn_id}/exec/{kind}/{path:path}/download")
async def download_cb_exec_file(
    tenant: str = Depends(get_tenant_dep),
    project: str = Depends(get_project),
    owner_id: str = "",
    conversation_id: str = "",
    turn_id: str = "",
    kind: str = "",
    path: str = "",
    session: UserSession = Depends(require_auth(RequireUser())),
):
    # owner or privileged only
    if not _is_owner_or_privileged(owner_id, session):
        raise HTTPException(status_code=403, detail="Forbidden")
    return await preview_cb_exec_file(tenant, project, owner_id, conversation_id, turn_id, kind, path, True, session)
