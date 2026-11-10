/*
 * SPDX-License-Identifier: MIT
 */

/**
 * Attachment limits the chat client applies before an upload.
 *
 * The server enforces per-image, per-PDF and total-message byte caps
 * (assembly.yaml `platform.attachments`) and publishes them in the runtime
 * config as `attachments.maxImageBytes`, `maxDocBytes` and `maxMessageBytes`.
 * Checking them in the picker turns an upload the server would reject into an
 * immediate message that names the file and the limit.
 *
 * `attachments.compressImages` (frontend.config, default false) downscales and
 * re-encodes large images to JPEG in the browser before the upload; see
 * utils/imageCompress.ts.
 *
 * This module has no imports so it runs unchanged under `node --test`.
 */

export interface AttachmentLimits {
    maxImageBytes: number;
    maxDocBytes: number;
    maxMessageBytes: number;
    compressImages: boolean;
}

const MB = 1024 * 1024;

/** The server defaults, used for any limit the runtime config does not publish. */
export const DEFAULT_ATTACHMENT_LIMITS: AttachmentLimits = {
    maxImageBytes: 5 * MB,
    maxDocBytes: 10 * MB,
    maxMessageBytes: 25 * MB,
    compressImages: false,
};

/** Image types the server measures against the per-image cap. */
export const CAPPED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);

const positiveInt = (value: unknown, fallback: number): number => {
    const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
};

export function resolveAttachmentLimits(raw: unknown): AttachmentLimits {
    const source = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
    return {
        maxImageBytes: positiveInt(source.maxImageBytes, DEFAULT_ATTACHMENT_LIMITS.maxImageBytes),
        maxDocBytes: positiveInt(source.maxDocBytes, DEFAULT_ATTACHMENT_LIMITS.maxDocBytes),
        maxMessageBytes: positiveInt(source.maxMessageBytes, DEFAULT_ATTACHMENT_LIMITS.maxMessageBytes),
        compressImages: source.compressImages === true,
    };
}

/**
 * Byte size a compressed image aims for: under the per-image cap, with room
 * for the multipart envelope.
 */
export function imageCompressionTarget(limits: AttachmentLimits): number {
    return Math.floor(limits.maxImageBytes * 0.9);
}

/**
 * The per-file cap a file is measured against. A capped image type the client
 * will compress first is held only to the total-message cap: refusing it at the
 * image cap would reject a file that fits once compressed.
 */
export function uploadCapForFile(file: { type?: string }, limits: AttachmentLimits): number {
    const type = (file?.type || "").toLowerCase();
    if (CAPPED_IMAGE_TYPES.has(type)) {
        return limits.compressImages ? limits.maxMessageBytes : limits.maxImageBytes;
    }
    if (type === "application/pdf") return limits.maxDocBytes;
    return limits.maxMessageBytes;
}

const asMb = (bytes: number): string => (bytes / MB).toFixed(1);

/** A rejection that names the file and the limit it exceeds. */
export function oversizeMessage(name: string, size: number, cap: number): string {
    return `"${name}" is ${asMb(size)} MB, over the ${asMb(cap)} MB limit. Please attach a smaller file.`;
}

/** Splits files into those within their cap and a message for each one over it. */
export function partitionBySizeCap<T extends { name: string; size: number }>(
    files: T[],
    capFor: (file: T) => number,
): { accepted: T[]; rejected: string[] } {
    const accepted: T[] = [];
    const rejected: string[] = [];
    for (const file of files) {
        const cap = capFor(file);
        if (file.size > cap) {
            rejected.push(oversizeMessage(file.name, file.size, cap));
        } else {
            accepted.push(file);
        }
    }
    return {accepted, rejected};
}
