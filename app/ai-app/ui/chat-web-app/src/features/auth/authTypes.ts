export type AuthType = "none" | "bundle" | "cognito" | "simple" | "hardcoded"

export interface AuthConfig {
    authType: AuthType;
}

export interface NoAuthConfig extends AuthConfig {
    authType: "none";
}

export interface BundleSessionAuthConfig extends AuthConfig {
    authType: "bundle";
    loginUrl?: string;
    connectionHub?: {
        bundleId?: string;
        authorityId?: string;
        providerId?: string;
        providerType?: string;
        entrypoint?: string;
    };
}

export interface SimpleAuthConfig extends AuthConfig {
    authType: "simple" | "hardcoded";
    token: string;
}

export interface CognitoAuthConfig extends AuthConfig {
    authType: "cognito";
    idTokenHeaderName: string;
    profileUrl?: string;
    logoutUrl?: string;
    oidcConfig: {
        authority:string;
        client_id:string;
        redirect_uri?:string;
        post_logout_redirect_uri?:string;
        scope?:string;
        [key:string]: unknown;
    }
}

export interface AppUser {
    name?: string;
    email?: string;
    roles?: string[];
    permissions?: string[];
    groups?: string[];
    username?: string;
    raw?: unknown;
}
