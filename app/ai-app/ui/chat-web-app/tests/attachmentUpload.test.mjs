import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";
import ts from "typescript";

// Attachment send path: picker caps that follow the server's published caps,
// bounded submit requests, opt-in image compression, and the composer clearing
// files the server rejected.

const testRoot = path.dirname(fileURLToPath(import.meta.url));
const source = (relative) => fs.readFileSync(path.resolve(testRoot, relative), "utf8");
const load = async (relative) => {
    const sourcePath = path.resolve(testRoot, relative);
    const compiled = ts.transpileModule(source(relative), {
        compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022},
        fileName: sourcePath,
    }).outputText;
    return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
};

const limits = await load("../src/features/chat/attachmentLimits.ts");
const timeouts = await load("../src/utils/fetchWithTimeout.ts");
const {compressImageForUpload} = await load("../src/utils/imageCompress.ts");

const MB = 1024 * 1024;

test("limits default to the server defaults and read the published caps", () => {
    assert.deepEqual(limits.resolveAttachmentLimits(undefined), {
        maxImageBytes: 5 * MB, maxDocBytes: 10 * MB, maxMessageBytes: 25 * MB, compressImages: false,
    });
    assert.deepEqual(
        limits.resolveAttachmentLimits({maxImageBytes: 10 * MB, maxDocBytes: "20971520", maxMessageBytes: -1, compressImages: true}),
        {maxImageBytes: 10 * MB, maxDocBytes: 20 * MB, maxMessageBytes: 25 * MB, compressImages: true},
    );
});

test("each file type is measured against the cap the server applies", () => {
    const plain = limits.resolveAttachmentLimits({});
    assert.equal(limits.uploadCapForFile({type: "image/png"}, plain), 5 * MB);
    assert.equal(limits.uploadCapForFile({type: "application/pdf"}, plain), 10 * MB);
    assert.equal(limits.uploadCapForFile({type: "text/csv"}, plain), 25 * MB);
    assert.equal(limits.uploadCapForFile({type: "image/heic"}, plain), 25 * MB);

    // With compression on, an image only has to fit the message once compressed.
    const compressing = limits.resolveAttachmentLimits({compressImages: true});
    assert.equal(limits.uploadCapForFile({type: "image/jpeg"}, compressing), 25 * MB);
    assert.equal(limits.imageCompressionTarget(compressing), Math.floor(5 * MB * 0.9));
});

test("oversized files are refused by name and limit; the rest are kept", () => {
    const plain = limits.resolveAttachmentLimits({});
    const files = [
        {name: "photo.png", size: 6 * MB, type: "image/png"},
        {name: "scan.pdf", size: 2 * MB, type: "application/pdf"},
    ];
    const {accepted, rejected} = limits.partitionBySizeCap(files, (f) => limits.uploadCapForFile(f, plain));
    assert.deepEqual(accepted.map((f) => f.name), ["scan.pdf"]);
    assert.deepEqual(rejected, ['"photo.png" is 6.0 MB, over the 5.0 MB limit. Please attach a smaller file.']);
});

test("the picker applies per-file caps when the caller passes no maxSize", () => {
    // The only call site passes maxSizeFor; a guard gated on maxSize alone never ran.
    const shared = source("../src/components/shared.ts");
    assert.match(shared, /const capFor = maxSize \? \(\) => maxSize : maxSizeFor;/);
    assert.match(source("../src/components/chat/ChatInterface/UserInput.tsx"), /maxSizeFor: \(file\) => uploadCapForFile\(file, attachmentLimits\)/);
});

test("a stalled submit fails with a readable timeout error", async () => {
    let signal;
    const hanging = (_input, init) => new Promise((_resolve, reject) => {
        signal = init.signal;
        init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
    await assert.rejects(
        timeouts.fetchWithTimeout("https://example.test/sse/chat", {method: "POST"}, 20, "Message submit", hanging),
        (err) => err instanceof timeouts.RequestTimeoutError && /Message submit did not complete within 0s/.test(err.message),
    );
    assert.equal(signal.aborted, true);
});

test("a prompt response and a non-timeout failure pass through unchanged", async () => {
    const ok = new Response("{}", {status: 200});
    assert.equal(await timeouts.fetchWithTimeout("https://example.test", {}, 1000, "x", async () => ok), ok);
    const boom = new TypeError("network down");
    await assert.rejects(timeouts.fetchWithTimeout("https://example.test", {}, 1000, "x", async () => { throw boom; }), boom);
});

test("the upload deadline grows with the payload", () => {
    assert.equal(timeouts.chatUploadTimeoutMs(0), 60_000);
    assert.ok(timeouts.chatUploadTimeoutMs(25 * MB) > timeouts.chatUploadTimeoutMs(5 * MB));
});

test("both transports bound the submit", () => {
    const sse = source("../src/features/chatController/sseChat.ts");
    assert.doesNotMatch(sse, /await fetch\(url, \{method: "POST", headers, body: form/);
    assert.equal((sse.match(/await fetchWithTimeout\(/g) || []).length, 2);
    assert.match(source("../src/features/chatController/socketIOChat.ts"), /this\._socket\.timeout\(timeoutMs\)\.emitWithAck\("chat_message"/);
});

test("compression leaves a file alone where it cannot or need not act", async () => {
    const small = new File([new Uint8Array(10)], "a.png", {type: "image/png"});
    assert.equal(await compressImageForUpload(small, 100), small);
    const text = new File([new Uint8Array(200)], "a.txt", {type: "text/plain"});
    assert.equal(await compressImageForUpload(text, 100), text);
    // node has no createImageBitmap: a large image is returned unchanged.
    const large = new File([new Uint8Array(200)], "a.png", {type: "image/png"});
    assert.equal(await compressImageForUpload(large, 100), large);
});

test("a rejected attachment clears the composer files", () => {
    const middleware = source("../src/features/chat/chatServiceMiddleware.ts");
    const start = middleware.indexOf('case "rate_limit.attachment_failure":');
    const body = middleware.slice(start, middleware.indexOf("break;", start));
    assert.match(body, /traits\.push\(\{type: UserInputAttachmentRejectedType\}\)/);
});
