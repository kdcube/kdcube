// Minimal client for the KB ingestion API exposed by the kb service
// (proxied by OpenResty under `/api/kb/...`). Used by the SearchSettingsPanel
// "Apply settings & ingest" flow.
//
// Auth: requests need the same Authorization / id-token headers the chat
// transport sends, otherwise the kb service returns 401. We reuse the
// existing `appendDefaultCredentialsHeader` helper.

import {appendDefaultCredentialsHeader} from "../../app/api/utils.ts";

export interface KBResourceMetadata {
    id: string;
    version: string | number;
    rn?: string;
    title?: string;
    mime?: string;
    [k: string]: unknown;
}

export interface KBUploadResponse {
    success: boolean;
    resource_id: string;
    resource_metadata: KBResourceMetadata;
    message?: string;
}

export interface KBProcessResponse {
    status: string;
    resource_id: string;
    task_id: string;
    message?: string;
}

export interface KBResourceListItem {
    id: string;
    version: string | number;
    title?: string;
    rn?: string;
    mime?: string;
    processing_status?: Record<string, boolean>;
    [k: string]: unknown;
}

const apiBase = (project: string) => `/api/kb/${encodeURIComponent(project)}`;

async function readJsonOrThrow<T>(res: Response): Promise<T> {
    if (!res.ok) {
        let detail = "";
        try {
            const body = await res.json();
            detail = body?.detail ?? JSON.stringify(body);
        } catch {
            try { detail = await res.text(); } catch { /* ignore */ }
        }
        throw new Error(`HTTP ${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
    }
    return res.json() as Promise<T>;
}

export async function uploadFile(project: string, file: File): Promise<KBUploadResponse> {
    const fd = new FormData();
    fd.append("file", file, file.name);
    // Don't set Content-Type — browser fills in the multipart boundary.
    const headers = appendDefaultCredentialsHeader({});
    const res = await fetch(`${apiBase(project)}/upload`, {
        method: "POST",
        body: fd,
        credentials: "include",
        headers,
    });
    return readJsonOrThrow<KBUploadResponse>(res);
}

export async function dispatchProcessing(
    project: string,
    resource: KBResourceMetadata,
    socketId: string,
    processingMode: string = "retrieval_only",
): Promise<KBProcessResponse> {
    const headers = appendDefaultCredentialsHeader({"Content-Type": "application/json"});
    const res = await fetch(`${apiBase(project)}/upload/process`, {
        method: "POST",
        credentials: "include",
        headers,
        body: JSON.stringify({
            resource_metadata: resource,
            socket_id: socketId,
            processing_mode: processingMode,
        }),
    });
    return readJsonOrThrow<KBProcessResponse>(res);
}

export async function listResources(project: string): Promise<KBResourceListItem[]> {
    const headers = appendDefaultCredentialsHeader({});
    const res = await fetch(`${apiBase(project)}/resources`, {
        method: "GET",
        credentials: "include",
        headers,
    });
    const body = await readJsonOrThrow<{resources?: KBResourceListItem[]} | KBResourceListItem[]>(res);
    if (Array.isArray(body)) return body;
    return body?.resources ?? [];
}

export type ResourceStage = "pending" | "extraction" | "segmentation" | "metadata" | "embedding" | "search_indexing" | "done";

export function deriveStage(item: KBResourceListItem): ResourceStage {
    const s = item.processing_status || {};
    if (s.search_indexing) return "done";
    if (s.embedding) return "search_indexing";
    if (s.metadata) return "embedding";
    if (s.segmentation) return "metadata";
    if (s.extraction) return "segmentation";
    return "pending";
}
