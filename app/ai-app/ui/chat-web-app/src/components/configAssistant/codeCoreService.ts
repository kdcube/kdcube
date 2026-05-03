/*
 * Direct HTTP client for the chat-proc /api/integrations/code-core/* endpoints.
 *
 * The Configuration Assistant inspect drawer uses this to fetch concept /
 * class details synchronously when the user clicks a graph node, rather
 * than asking the LLM to re-run code_graph.* tools.
 */
import {store} from "../../app/store.ts";
import {selectAuthToken, selectIdToken} from "../../features/auth/authSlice.ts";
import {selectChatSettingsLoaded} from "../../features/chat/chatSettingsSlice.ts";

const BASE = "/api/integrations/code-core";

interface SemanticMatch {
    id?: string;
    kind?: string;
    scope?: string;
    name?: string;
    aliases?: string[];
    category?: string;
    summary?: string;
    definition?: string;
    rationale?: string;
    how_to_apply?: string;
    pitfalls?: string[];
    related?: Array<{id?: string; name?: string; kind?: string; scope?: string}>;
    realized_by?: string[];
    applied_to?: string[];
}

export interface DefineResponse {
    matches?: SemanticMatch[];
    error?: string;
}

export interface FootprintRecord {
    name?: string;
    qualified_name?: string;
    docstring?: string;
    file_path?: string;
    ancestors?: string[];
    descendants?: string[];
    methods?: Array<{name?: string; signature?: string; docstring?: string; is_abstract?: boolean}>;
    properties?: string[];
    callers?: string[];
    callees?: string[];
    tests?: string[];
}

interface SemanticBadge {
    id?: string;
    name?: string;
    summary?: string;
}

export interface ClassFootprintResponse {
    footprint?: FootprintRecord[];
    concepts?: SemanticBadge[];
    style_policies?: SemanticBadge[];
    error?: string;
}

const buildHeaders = (): HeadersInit => {
    const state = store.getState();
    const headers: Record<string, string> = {Accept: "application/json"};
    const token = selectAuthToken(state);
    if (token) {
        headers["Authorization"] = `Bearer ${token}`;
    }
    const idToken = selectIdToken(state);
    if (idToken && selectChatSettingsLoaded(state)) {
        const headerName = (state.chatSettings.settings.auth as unknown as
            {idTokenHeaderName?: string}).idTokenHeaderName;
        if (headerName) {
            headers[headerName] = idToken;
        }
    }
    return headers;
};

const handle = async <T>(res: Response): Promise<T> => {
    if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`code-core ${res.status}: ${body || res.statusText}`);
    }
    return (await res.json()) as T;
};

export async function fetchDefine(term: string, scope?: string): Promise<DefineResponse> {
    const params = new URLSearchParams({term});
    if (scope) params.set("scope", scope);
    const res = await fetch(`${BASE}/define?${params.toString()}`, {
        method: "GET",
        headers: buildHeaders(),
    });
    return handle<DefineResponse>(res);
}

export async function fetchClassFootprint(qualifiedName: string): Promise<ClassFootprintResponse> {
    const params = new URLSearchParams({qualified_name: qualifiedName});
    const res = await fetch(`${BASE}/class_footprint?${params.toString()}`, {
        method: "GET",
        headers: buildHeaders(),
    });
    return handle<ClassFootprintResponse>(res);
}
