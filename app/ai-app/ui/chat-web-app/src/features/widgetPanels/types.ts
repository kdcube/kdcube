export interface PanelResponse {
    status: string,
    tenant: string,
    project: string,
    bundle_id: string,
    conversation_id: string | null,
}

export interface EconomicsResponse extends PanelResponse {
    control_plane: string[];
}

export interface AIBundlesResponse extends PanelResponse {
    ai_bundles: string[];
}

export interface GatewayResponse extends PanelResponse {
    svc_gateway: string[];
}

export interface ConversationsBrowserResponse extends PanelResponse {
    conversation_browser: string[];
}

export interface RedisBrowserResponse extends PanelResponse {
    redis_browser: string[];
}

export interface EconomicUsageResponse extends PanelResponse {
    economic_usage: string[];
}

export interface VersatilePreferencesResponse extends PanelResponse {
    preferences_widget: string[];
}
