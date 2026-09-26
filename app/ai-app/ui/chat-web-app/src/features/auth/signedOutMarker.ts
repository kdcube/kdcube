// The signed-out route sends the browser back with ?signed_out=1 (W260).
// The app starts signed out on that load, so it does not begin a login by
// itself, and removes the marker so a later reload behaves normally.

export const SIGNED_OUT_MARKER = "signed_out";

interface MarkerLocation {
    pathname: string;
    search: string;
    hash: string;
}

interface MarkerHistory {
    state: unknown;
    replaceState: (data: unknown, unused: string, url?: string | URL | null) => void;
}

export function takeSignedOutMarker(location?: MarkerLocation, history?: MarkerHistory): boolean {
    if (!location) {
        return false;
    }
    const params = new URLSearchParams(location.search || "");
    if (params.get(SIGNED_OUT_MARKER) !== "1") {
        return false;
    }
    params.delete(SIGNED_OUT_MARKER);
    const query = params.toString();
    try {
        history?.replaceState(history.state, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash || ""}`);
    } catch {
        // The marker stays in the address bar; the state is still signed out.
    }
    return true;
}
