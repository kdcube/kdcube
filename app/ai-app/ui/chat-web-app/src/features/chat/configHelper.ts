import {selectChatPath, selectRoutesPrefix} from "./chatSettingsSlice.ts";
import {store} from "../../app/store.ts";

export const getDefaultRoutePrefix = () => {
    return selectRoutesPrefix(store.getState())
}

export function getChatPagePath() {
    return selectChatPath(store.getState())
}