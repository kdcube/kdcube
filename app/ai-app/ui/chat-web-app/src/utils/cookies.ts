export interface CookieOptions {
    expires?: number | Date;
    path?: string;
    domain?: string;
    secure?: boolean;
    sameSite?: 'Strict' | 'Lax' | 'None';
}

export function setCookie(name:string, value:string, opts:CookieOptions = {}) {
    if (!name || /[=;,\s]/.test(name)) {
        throw new Error(`Invalid cookie name: "${name}"`);
    }

    let cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}`;

    if (opts.expires !== undefined) {
        const date =
            opts.expires instanceof Date
                ? opts.expires
                : new Date(Date.now() + opts.expires * 864e5); // days → ms
        cookie += `; expires=${date.toUTCString()}`;
    }

    cookie += `; path=${opts.path ?? '/'}`;

    if (opts.domain)   cookie += `; domain=${opts.domain}`;
    if (opts.secure)   cookie += `; secure`;
    cookie += `; samesite=${opts.sameSite ?? 'Lax'}`;

    document.cookie = cookie;
}

export function getCookie(name:string) {
    const key = encodeURIComponent(name);
    const match = document.cookie
        .split('; ')
        .find(pair => pair.startsWith(`${key}=`));
    return match ? decodeURIComponent(match.slice(key.length + 1)) : null;
}

export function hasCookie(name:string) {
    return getCookie(name) !== null;
}

export function removeCookie(name:string, opts:Pick<CookieOptions, 'path'|'domain'> = {}) {
    setCookie(name, '', {
        ...opts,
        expires: new Date(0), // epoch → immediately expired
    });
}

export function getAllCookies() {
    if (!document.cookie) return {};
    return Object.fromEntries(
        document.cookie.split('; ').map(pair => {
            const idx = pair.indexOf('=');
            return [
                decodeURIComponent(pair.slice(0, idx)),
                decodeURIComponent(pair.slice(idx + 1)),
            ];
        })
    );
}

export function clearAllCookies() {
    Object.keys(getAllCookies()).forEach(name => removeCookie(name));
}

export function setJsonCookie(name:string, value:unknown, opts:CookieOptions = {}) {
    setCookie(name, JSON.stringify(value), opts);
}

export function getJsonCookie(name:string, fallback:unknown = null):unknown {
    const raw = getCookie(name);
    if (raw === null) return fallback;
    try {
        return JSON.parse(raw);
    } catch {
        return fallback;
    }
}

export function areCookiesEnabled() {
    const test = '__cookie_test__';
    try {
        setCookie(test, '1');
        const ok = hasCookie(test);
        removeCookie(test);
        return ok;
    } catch {
        return false;
    }
}