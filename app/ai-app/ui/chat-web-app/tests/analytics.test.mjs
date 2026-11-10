import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";
import ts from "typescript";

// Web analytics is opt-in: without an `analytics` section in the runtime
// config nothing is loaded and no event is sent.

const testRoot = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(testRoot, "../src/utils/analytics.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
    compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022},
    fileName: sourcePath,
}).outputText;
const analytics = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

const fakeHost = () => {
    const appended = [];
    const document = {
        title: "Chat",
        head: {appendChild: (node) => appended.push(node)},
        createElement: (tag) => ({tag}),
    };
    const window = {location: {pathname: "/chat", search: "?a=1", hash: "", href: "https://example.test/chat?a=1"}};
    return {host: {document, window}, appended, window};
};

test("only a google provider with a well-formed measurement id enables analytics", () => {
    assert.equal(analytics.resolveAnalyticsConfig(undefined), null);
    assert.equal(analytics.resolveAnalyticsConfig({}), null);
    assert.equal(analytics.resolveAnalyticsConfig({provider: "google"}), null);
    assert.equal(analytics.resolveAnalyticsConfig({provider: "google", measurementId: "UA-1234"}), null);
    assert.equal(analytics.resolveAnalyticsConfig({provider: "other", measurementId: "G-ABC1234"}), null);
    assert.deepEqual(
        analytics.resolveAnalyticsConfig({provider: "Google", measurementId: " G-ABC1234 "}),
        {provider: "google", measurementId: "G-ABC1234"},
    );
});

test("without a config nothing is loaded and events are dropped", () => {
    const {host, appended, window} = fakeHost();

    assert.equal(analytics.initAnalytics(undefined, host), false);
    analytics.trackAnalyticsEvent("chat_send", {attachments_count: 1});

    assert.deepEqual(appended, []);
    assert.equal(window.dataLayer, undefined);
    assert.equal(window.gtag, undefined);
});

test("an enabled config loads the tag once, reports the page view, then events", () => {
    const {host, appended, window} = fakeHost();

    assert.equal(analytics.initAnalytics({provider: "google", measurementId: "G-ABC1234"}, host), true);
    assert.equal(analytics.initAnalytics({provider: "google", measurementId: "G-ABC1234"}, host), true);
    analytics.trackAnalyticsEvent("chat_send", {attachments_count: 2});

    assert.equal(appended.length, 1);
    assert.equal(appended[0].src, "https://www.googletagmanager.com/gtag/js?id=G-ABC1234");
    const calls = window.dataLayer.map((args) => Array.from(args));
    assert.deepEqual(calls[1], ["config", "G-ABC1234", {send_page_view: false}]);
    assert.equal(calls[2][0], "event");
    assert.equal(calls[2][1], "page_view");
    assert.equal(calls[2][2].page_path, "/chat?a=1");
    assert.deepEqual(calls[3], ["event", "chat_send", {attachments_count: 2}]);
});

test("the build ships no measurement id", () => {
    const html = fs.readFileSync(path.resolve(testRoot, "../index.html"), "utf8");
    assert.doesNotMatch(html, /googletagmanager|G-[A-Z0-9]{4,}/);
});
