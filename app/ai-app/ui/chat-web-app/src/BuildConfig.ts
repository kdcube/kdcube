/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

function selectValue<T>(...args: T[]) {
    for (const arg of args) {
        if (arg === undefined || arg === null)
            continue
        return arg;
    }
    return null;
}

function selectBool(...args: (boolean | undefined | null)[]) {
    for (const arg of args) {
        if (arg === undefined || arg === null)
            continue
        return Boolean(arg);
    }
    return false;
}

export const chatAPIBasePath = selectValue<string>(import.meta.env.CHAT_WEB_APP_CHAT_API_BASE_PATH, '') as string
export const configPath = selectValue<string>(import.meta.env.CHAT_WEB_APP_CONFIG_FILE_PATH, "/config.json") as string
export const showDebugControls = selectBool(import.meta.env.CHAT_WEB_APP_SHOW_DEBUG_CONTROLS, false)
