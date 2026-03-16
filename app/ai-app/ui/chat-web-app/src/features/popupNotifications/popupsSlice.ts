import {createSlice, PayloadAction} from "@reduxjs/toolkit";
import {AppNotification, PopupNotificationsState} from "./types.ts";

const popupNotificationsSlice = createSlice({
    name: 'popupNotifications',
    initialState: (): PopupNotificationsState => {
        return {
            messages: []
        }
    },
    reducers: {
        pushNotification(state: PopupNotificationsState, action: PayloadAction<AppNotification>) {
            state.messages.push(action.payload);
        },
        resetPopupNotifications(state) {
            state.messages = []
        },
    }
})

export const {resetPopupNotifications, pushNotification} = popupNotificationsSlice.actions;

export default popupNotificationsSlice.reducer