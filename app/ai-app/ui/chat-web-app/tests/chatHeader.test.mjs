import assert from "node:assert/strict";
import fs from "node:fs";
import {createRequire} from "node:module";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";
import vm from "node:vm";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import ts from "typescript";

const testRoot = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(testRoot, "../src/features/header/ChatHeader.tsx");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
    compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
    },
    fileName: sourcePath,
}).outputText;
const require = createRequire(import.meta.url);
const inert = () => null;

// Render the actual header with its query/state boundaries supplied by the test.
// React and its select/option rendering are real; unrelated controls do no work.
function renderHeader(data, currentBundle) {
    const selectors = {
        selectChatConnected: () => true,
        selectChatStayConnected: () => true,
        selectAppUser: () => null,
        selectProject: () => "project",
        selectTenant: () => "tenant",
        selectCurrentBundle: () => currentBundle,
    };
    const modules = {
        "react": React,
        "react/jsx-runtime": require("react/jsx-runtime"),
        "lucide-react": {Blocks: inert, Loader: inert, LogOut: inert, Wifi: inert, WifiOff: inert},
        "../../app/store.ts": {useAppDispatch: () => inert, useAppSelector: selector => selector()},
        "../chat/chatStateSlice.ts": selectors,
        "../auth/authMiddleware.ts": {logOut: inert},
        "../auth/authSlice.ts": selectors,
        "../bundles/bundlesAPI.ts": {useGetBundlesListQuery: () => ({data, isSuccess: true})},
        "../chat/chatSettingsSlice.ts": selectors,
        "../bundles/bundlesSlice.ts": {...selectors, setCurrentBundle: inert},
        "../../components/IconContainer.tsx": {default: inert},
        "./BundleControls.tsx": {default: inert},
        "../chatSidePanel/sidePanelContext.ts": {useSidePanelContext: () => ({setPanelId: inert})},
        "../../controlPlaneMount.ts": {controlPlanePublicPath: value => `/${value}`},
    };
    const exports = {};
    vm.runInNewContext(compiled, {
        exports,
        require: name => {
            assert.ok(Object.hasOwn(modules, name), `Unmocked header boundary: ${name}`);
            return modules[name];
        },
    }, {filename: sourcePath});
    return renderToStaticMarkup(React.createElement(exports.default));
}

const bundleId = "example-app@2026-11-04";
const otherId = "other-app@1-0";
const cases = [
    {label: "omitted", entry: {}, expected: bundleId},
    {label: "null", entry: {name: null}, expected: bundleId},
    {label: "empty", entry: {name: ""}, expected: bundleId},
    {label: "whitespace-only", entry: {name: " \t\n "}, expected: bundleId},
    {label: "explicit", entry: {name: "  Custom App  "}, expected: "  Custom App  "},
];

for (const {label, entry, expected} of cases) {
    for (const selectedIsDefault of [false, true]) {
        test(`${label} name renders with stable option values and selection; selected default=${selectedIsDefault}`, () => {
            const data = {
                defaultBundle: selectedIsDefault ? bundleId : otherId,
                bundles: {
                    [bundleId]: {id: bundleId, ...entry},
                    [otherId]: {id: otherId, name: "Other App"},
                },
            };
            const original = JSON.stringify(data);
            const html = renderHeader(data, bundleId);
            const options = Array.from(html.matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g), ([, attrs, text]) => ({
                value: attrs.match(/\bvalue="([^"]*)"/)?.[1],
                selected: /\bselected(?:=|\s|$)/.test(attrs),
                text,
            }));

            assert.deepEqual(options, [
                {value: bundleId, selected: true, text: expected + (selectedIsDefault ? " (default)" : "")},
                {value: otherId, selected: false, text: "Other App" + (selectedIsDefault ? "" : " (default)")},
            ]);
            assert.equal(JSON.stringify(data), original, "Rendering preserves the supplied metadata");
        });
    }
}
