/*
 * SPDX-License-Identifier: MIT
 */

/**
 * Pre-upload image downscale, enabled by `attachments.compressImages` in the
 * runtime config.
 *
 * A phone photo is often larger than the per-image cap. With compression on,
 * an image above `maxBytes` is scaled so its longer edge is at most `maxDim`
 * pixels and re-encoded as JPEG before it leaves the browser. Anything else
 * (non-images, small images, a browser without `createImageBitmap` or a 2D
 * canvas, a decode failure, a result that is still too large) returns the
 * original File, and the server's cap decides.
 *
 * This module has no imports so it runs unchanged under `node --test`.
 */
export async function compressImageForUpload(
    file: File,
    maxBytes: number,
    maxDim = 2048,
    quality = 0.85,
): Promise<File> {
    if (!file.type.startsWith("image/") || file.size <= maxBytes) return file
    if (typeof createImageBitmap !== "function") return file

    let bmp: ImageBitmap
    try {
        bmp = await createImageBitmap(file)
    } catch {
        return file
    }

    try {
        const scale = Math.min(1, maxDim / Math.max(bmp.width, bmp.height))
        const w = Math.max(1, Math.round(bmp.width * scale))
        const h = Math.max(1, Math.round(bmp.height * scale))

        let blob: Blob | null = null
        if (typeof OffscreenCanvas !== "undefined") {
            const canvas = new OffscreenCanvas(w, h)
            const ctx = canvas.getContext("2d")
            if (!ctx) return file
            ctx.drawImage(bmp, 0, 0, w, h)
            blob = await canvas.convertToBlob({type: "image/jpeg", quality})
        } else if (typeof document !== "undefined") {
            const canvas = document.createElement("canvas")
            canvas.width = w
            canvas.height = h
            const ctx = canvas.getContext("2d")
            if (!ctx) return file
            ctx.drawImage(bmp, 0, 0, w, h)
            blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality))
        } else {
            return file
        }

        if (!blob || blob.size >= file.size) return file

        return new File(
            [blob],
            file.name.replace(/\.[^./\\]+$/, "") + ".jpg",
            {type: "image/jpeg"},
        )
    } catch {
        return file
    } finally {
        bmp.close?.()
    }
}
