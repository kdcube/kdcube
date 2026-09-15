import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";
import ts from "typescript";


const testRoot = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(testRoot, "../src/features/chat/chatSettingsLoader.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
    compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2022,
    },
    fileName: sourcePath,
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {loadRuntimeChatSettings} = await import(moduleUrl);

const jsonResponse = (value, status = 200) => new Response(JSON.stringify(value), {
    status,
    headers: {"content-type": "application/json"},
});

test("retries authoritative runtime config without reading stale static auth", async () => {
    const calls = [];
    const runtimeSettings = {auth: {authType: "bundle"}, tenant: "demo", project: "demo"};

    const settings = await loadRuntimeChatSettings({
        configEndpoint: "/api/cp-frontend-config",
        configPath: "/platform/config.json",
        fetchImpl: async (input) => {
            calls.push(String(input));
            return calls.length === 1
                ? jsonResponse({detail: "client closed request"}, 499)
                : jsonResponse(runtimeSettings);
        },
    });

    assert.deepEqual(settings, runtimeSettings);
    assert.deepEqual(calls, ["/api/cp-frontend-config", "/api/cp-frontend-config"]);
});

test("uses static config only when the runtime endpoint is absent", async () => {
    const calls = [];
    const staticSettings = {auth: {authType: "none"}, tenant: "local", project: "local"};

    const settings = await loadRuntimeChatSettings({
        configEndpoint: "/api/cp-frontend-config",
        configPath: "/platform/config.json",
        fetchImpl: async (input) => {
            calls.push(String(input));
            return calls.length === 1
                ? jsonResponse({detail: "not found"}, 404)
                : jsonResponse(staticSettings);
        },
    });

    assert.deepEqual(settings, staticSettings);
    assert.deepEqual(calls, ["/api/cp-frontend-config", "/platform/config.json"]);
});

test("does not replace repeated runtime failures with static authentication", async () => {
    const calls = [];

    await assert.rejects(
        loadRuntimeChatSettings({
            configEndpoint: "/api/cp-frontend-config",
            configPath: "/platform/config.json",
            fetchImpl: async (input) => {
                calls.push(String(input));
                return jsonResponse({detail: "unavailable"}, 503);
            },
        }),
        /Chat settings request failed: \/api\/cp-frontend-config \(503\)/,
    );
    assert.deepEqual(calls, ["/api/cp-frontend-config", "/api/cp-frontend-config"]);
});
