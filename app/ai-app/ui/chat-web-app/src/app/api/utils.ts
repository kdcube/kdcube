import {handleContentDownload} from "../../components/shared.ts";
import {getDefaultAuthToken} from "../../features/auth/helpers.ts";
import {chatAPIBasePath} from "../../BuildConfig.ts";

export function appendCredentials(accessToken: string | null | undefined, headers?: HeadersInit) {
    if (!headers) {
        headers = {};
    }
    if (accessToken) {
        if (headers instanceof Headers) {
            headers.set("Authorization", `Bearer ${accessToken}`);
        } else if (headers instanceof Array) {
            headers.push(["Authorization", `Bearer ${accessToken}`]);
        } else {
            headers["Authorization"] = `Bearer ${accessToken}`
        }
    }
    return headers;
}

export function appendDefaultCredentialsHeader(headers?: HeadersInit) {
    return appendCredentials(getDefaultAuthToken(), headers);
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

export const downloadBlob = async (path: string, accessToken?: string | null) => {
    const headers = appendCredentials(accessToken === undefined ? getDefaultAuthToken() : accessToken);
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