// The bundle-session logout sequence, kept free of the store so its order is testable.
//
// The platform session ends on the server (the cookie is HttpOnly), then the
// identity provider must forget the browser too. The upstream sign-out
// navigation has to happen before, and instead of, any signed-out state: that
// state makes the app start a new login, and a new login against a provider
// that still has its own session signs the browser straight back in
// (2026-09-26, platform chat on dev-main: POST /api/platform/logout, then
// GET /api/platform/session/login, then a silent callback).

export interface BundleLogoutSteps {
    // POST the platform logout; resolves to the provider sign-out URL, or "" when there is none.
    postLogout: () => Promise<string>;
    removeCookies: () => void;
    // Leave the page for the provider's sign-out; the browser returns through the signed-out route.
    navigate: (url: string) => void;
    // Show the signed-out page, which offers Sign in and never starts a login by itself.
    markSignedOut: () => void;
}

export async function runBundleLogout(steps: BundleLogoutSteps): Promise<"upstream" | "signed_out"> {
    let upstreamLogoutUrl = "";
    try {
        upstreamLogoutUrl = String(await steps.postLogout() || "").trim();
    } catch (error) {
        console.warn("Platform logout request failed", error);
    }
    steps.removeCookies();
    if (upstreamLogoutUrl) {
        steps.navigate(upstreamLogoutUrl);
        return "upstream";
    }
    steps.markSignedOut();
    return "signed_out";
}
