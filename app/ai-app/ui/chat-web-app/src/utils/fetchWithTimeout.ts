/*
 * SPDX-License-Identifier: MIT
 */

/**
 * Bounds on the chat submit requests.
 *
 * A bare `fetch` (or Socket.IO ack) has no deadline. When the request stalls
 * (a half-open socket, a proxy that accepted the body and never answered, a
 * Socket.IO server that dropped an oversized message) the promise never
 * settles, neither the success nor the error path runs, and the turn stays
 * "sending" forever. A bounded request fails with a readable error instead.
 *
 * Upload time grows with the payload, so the attachment bound is a base plus
 * an allowance per byte at a slow-link rate.
 *
 * This module has no imports so it runs unchanged under `node --test`.
 */

export const CHAT_SUBMIT_TIMEOUT_MS = 60_000;
const UPLOAD_BASE_TIMEOUT_MS = 60_000;
const UPLOAD_MIN_BYTES_PER_SECOND = 100 * 1024;

/** Deadline for a chat submit carrying `bytes` of attachments. */
export function chatUploadTimeoutMs(bytes: number): number {
    const size = Number.isFinite(bytes) && bytes > 0 ? bytes : 0;
    return UPLOAD_BASE_TIMEOUT_MS + Math.ceil((size / UPLOAD_MIN_BYTES_PER_SECOND) * 1000);
}

export class RequestTimeoutError extends Error {
    constructor(label: string, timeoutMs: number) {
        super(
            `${label} did not complete within ${Math.round(timeoutMs / 1000)}s. `
            + "The connection may be slow or interrupted; please try again.",
        );
        this.name = "RequestTimeoutError";
    }
}

/** `fetch` that aborts after `timeoutMs` and rejects with a RequestTimeoutError. */
export async function fetchWithTimeout(
    input: URL | RequestInfo,
    init: RequestInit,
    timeoutMs: number,
    label: string,
    fetchImpl: typeof fetch = fetch,
): Promise<Response> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);
    try {
        return await fetchImpl(input, {...init, signal: controller.signal});
    } catch (err) {
        if (timedOut) throw new RequestTimeoutError(label, timeoutMs);
        throw err;
    } finally {
        clearTimeout(timer);
    }
}

