import type {ChatSettings} from "./chatTypes.ts";


type ChatSettingsFetchResult =
    | {kind: "loaded"; settings: ChatSettings}
    | {kind: "endpoint-unavailable"};

export interface ChatSettingsLoaderOptions {
    configEndpoint: string;
    configPath: string;
    fetchImpl?: typeof fetch;
}

const fetchChatSettings = async (
    fetchImpl: typeof fetch,
    path: string,
    endpointMayBeAbsent = false,
): Promise<ChatSettingsFetchResult> => {
    const response = await fetchImpl(path, {cache: "no-store"});
    if (endpointMayBeAbsent && response.status === 404) {
        return {kind: "endpoint-unavailable"};
    }
    if (!response.ok) {
        throw new Error(`Chat settings request failed: ${path} (${response.status})`);
    }
    const contentType = response.headers.get("content-type")?.toLowerCase() || "";
    if (endpointMayBeAbsent && !contentType.includes("application/json")) {
        return {kind: "endpoint-unavailable"};
    }
    return {kind: "loaded", settings: await response.json() as ChatSettings};
}

export const loadRuntimeChatSettings = async ({
    configEndpoint,
    configPath,
    fetchImpl = fetch,
}: ChatSettingsLoaderOptions): Promise<ChatSettings> => {
    let primary: ChatSettingsFetchResult | null = null;
    let primaryError: unknown = null;

    // Mobile browsers can interrupt a request while resuming or reloading.
    // Retry the descriptor-owned endpoint before declaring startup failure.
    for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
            primary = await fetchChatSettings(fetchImpl, configEndpoint, true);
            primaryError = null;
            break;
        } catch (error) {
            primaryError = error;
        }
    }

    if (primary?.kind === "loaded") {
        return primary.settings;
    }
    if (primaryError) {
        throw primaryError;
    }
    if (primary?.kind === "endpoint-unavailable" && configPath !== configEndpoint) {
        const fallback = await fetchChatSettings(fetchImpl, configPath);
        if (fallback.kind === "loaded") {
            return fallback.settings;
        }
    }
    throw new Error("Could not load chatSettings from server");
}
