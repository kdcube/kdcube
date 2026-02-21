import {handleContentDownload} from "../../components/shared.ts";
import {getDefaultAuthToken, getDefaultIdToken, getIdTokenHeaderName} from "../../features/auth/helpers.ts";
import {chatAPIBasePath} from "../../BuildConfig.ts";

export function appendHeader(name: string, value: string, headers?: HeadersInit) {
    if (!headers) {
        headers = {};
    }
    if (headers instanceof Headers) {
        headers.set(name, value);
    } else if (headers instanceof Array) {
        headers.push([name, value]);
    } else {
        headers[name] = value
    }
    return headers;
}

export function appendCredentials(headers?: HeadersInit, accessToken?: string | null | undefined, idToken?: string | null) {
    if (!headers) {
        headers = {};
    }
    if (accessToken) {
        headers = appendHeader("Authorization", `Bearer ${accessToken}`, headers)
    }
    const headerName = getIdTokenHeaderName();
    if (idToken && headerName) {
        headers = appendHeader(headerName, idToken, headers)
    }
    return headers;
}

export function appendDefaultCredentialsHeader(headers?: HeadersInit, overrideAccessToken?: string | null, overrideIddToken?: string | null) {
    return appendCredentials(headers, overrideAccessToken ?? getDefaultAuthToken(), overrideIddToken ?? getDefaultIdToken());
}

export const getResourceByRN = async (rn: string) => {
    const headers = appendDefaultCredentialsHeader([["Content-Type", "application/json"]]);
    const res = await fetch(
        `${chatAPIBasePath}/api/cb/resources/by-rn`,
        {method: "POST", headers, body: JSON.stringify({rn: rn})}
    );
    if (!res.ok) {
        throw new Error("Failed to get resource by rn");
    }
    return await res.json();
};

export const downloadBlob = async (path: string, accessToken?: string | null, idToken?: string | null) => {
    const headers = appendDefaultCredentialsHeader({}, accessToken, idToken);
    const res = await fetch(
        `${chatAPIBasePath}${path}`,
        {headers}
    );
    if (!res.ok) {
        throw new Error("Failed to get resource by rn");
    }
    return await res.blob()
};

export const downloadResourceByRN = async (rn: string, fileName: string, mimeType?: string | null) => {
    const resource = await getResourceByRN(rn)
    const download_url = resource.metadata.download_url
    const data = await downloadBlob(download_url)
    handleContentDownload(fileName, data, mimeType || "application/octet-stream")
}