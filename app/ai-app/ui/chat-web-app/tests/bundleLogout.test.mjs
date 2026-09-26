import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";
import ts from "typescript";

// W260: logout must reach the identity provider's sign-out before any
// signed-out state, because that state starts a new login and the provider's
// live session signs the browser straight back in.

const testRoot = path.dirname(fileURLToPath(import.meta.url));
const load = async (relative) => {
    const sourcePath = path.resolve(testRoot, relative);
    const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
        compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022},
        fileName: sourcePath,
    }).outputText;
    return import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
};
const {runBundleLogout} = await load("../src/features/auth/bundleLogout.ts");

const recorder = (upstream) => {
    const calls = [];
    return {
        calls,
        steps: {
            postLogout: async () => {
                calls.push("post");
                if (upstream instanceof Error) throw upstream;
                return upstream;
            },
            removeCookies: () => calls.push("cookies"),
            navigate: (url) => calls.push(`navigate ${url}`),
            markSignedOut: () => calls.push("signed_out"),
        },
    };
};

test("with a provider sign-out URL the browser goes there, and no signed-out state starts a login", async () => {
    const url = "https://auth.example.test/logout?client_id=x&logout_uri=https%3A%2F%2Fapp%2Fapi%2Fplatform%2Fsession%2Fsigned-out";
    const {calls, steps} = recorder(url);
    assert.equal(await runBundleLogout(steps), "upstream");
    assert.deepEqual(calls, ["post", "cookies", `navigate ${url}`]);
});

test("without a provider sign-out URL the signed-out page is shown instead", async () => {
    for (const upstream of ["", "   ", new Error("network down")]) {
        const {calls, steps} = recorder(upstream);
        assert.equal(await runBundleLogout(steps), "signed_out");
        assert.deepEqual(calls, ["post", "cookies", "signed_out"]);
    }
});

test("the middleware runs the sequence and never dispatches setLoggedOut on logout", () => {
    const middleware = fs.readFileSync(path.resolve(testRoot, "../src/features/auth/authMiddleware.ts"), "utf8");
    const start = middleware.indexOf("const endBundleSession");
    const body = middleware.slice(start, middleware.indexOf("const verifyBundleSession"));
    assert.ok(body.includes("runBundleLogout("));
    assert.ok(!body.includes("setLoggedOut"), "setLoggedOut starts a login before the provider sign-out");
    assert.ok(body.includes("markSignedOut: () => store.dispatch(setSignedOut())"));
});

test("an explicit sign-out waits for Sign in instead of starting a login", () => {
    const gate = fs.readFileSync(path.resolve(testRoot, "../src/features/auth/WithAuthRequired.tsx"), "utf8");
    assert.match(gate, /if \(!loggedIn && !loading && !signedOut\)/);
    assert.match(gate, /You are signed out\./);
});
