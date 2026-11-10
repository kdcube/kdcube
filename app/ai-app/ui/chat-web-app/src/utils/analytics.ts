/*
 * SPDX-License-Identifier: MIT
 */

/**
 * Opt-in web analytics for the chat app.
 *
 * Off unless the runtime config carries a supported `analytics` section
 * (assembly.yaml `frontend.config.analytics`):
 *
 *     analytics:
 *       provider: google
 *       measurementId: G-XXXXXXXXXX
 *
 * When it is on, the app loads the provider's tag once, reports one page view
 * and reports chat activity as events whose parameters describe the request
 * shape (bundle, counts, flags), never message text or file names. With no
 * section, nothing is loaded and `trackAnalyticsEvent` does nothing.
 *
 * This module has no imports so it runs unchanged under `node --test`.
 */

type GtagFn = (...args: unknown[]) => void;

interface AnalyticsHost {
    document: Document;
    window: Window & { dataLayer?: unknown[]; gtag?: GtagFn };
}

export interface AnalyticsConfig {
    provider: "google";
    measurementId: string;
}

const GOOGLE_MEASUREMENT_ID = /^G-[A-Z0-9]{4,20}$/;

let active: { gtag: GtagFn } | null = null;

/** The analytics config the runtime config enables, or null when analytics is off. */
export function resolveAnalyticsConfig(raw: unknown): AnalyticsConfig | null {
    if (!raw || typeof raw !== "object") return null;
    const section = raw as Record<string, unknown>;
    const provider = String(section.provider ?? "").trim().toLowerCase();
    const measurementId = String(section.measurementId ?? "").trim();
    if (provider !== "google" || !GOOGLE_MEASUREMENT_ID.test(measurementId)) return null;
    return {provider: "google", measurementId};
}

/**
 * Loads the provider tag and reports the page view. Idempotent: only the first
 * call with an enabled config has an effect. Returns whether analytics is on.
 */
export function initAnalytics(raw: unknown, host?: AnalyticsHost): boolean {
    if (active) return true;
    const config = resolveAnalyticsConfig(raw);
    if (!config) return false;
    const env: AnalyticsHost | null = host ?? (typeof window !== "undefined" ? {document, window} : null);
    if (!env) return false;

    const {document: doc, window: win} = env;
    win.dataLayer = win.dataLayer || [];
    const gtag: GtagFn = function () {
        // gtag.js reads the arguments object itself, not an array copy.
        // eslint-disable-next-line prefer-rest-params
        win.dataLayer!.push(arguments);
    };
    win.gtag = gtag;

    const script = doc.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(config.measurementId)}`;
    doc.head.appendChild(script);

    gtag("js", new Date());
    gtag("config", config.measurementId, {send_page_view: false});
    gtag("event", "page_view", {
        page_path: `${win.location.pathname}${win.location.search}${win.location.hash}`,
        page_location: win.location.href,
        page_title: doc.title,
    });
    active = {gtag};
    return true;
}

/** Reports one event when analytics is on; does nothing otherwise. */
export function trackAnalyticsEvent(name: string, params?: Record<string, unknown>): void {
    active?.gtag("event", name, params ?? {});
}
