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

export const chatAPIBasePath = selectValue(import.meta.env.CHAT_WEB_APP_CHAT_API_BASE_PATH, '') as string


