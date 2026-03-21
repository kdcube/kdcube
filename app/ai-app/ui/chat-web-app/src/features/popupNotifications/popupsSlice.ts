import {createSlice, PayloadAction} from "@reduxjs/toolkit";
import {PopupNotificationsState, NewNotification} from "./types.ts";
import type {RootState} from "../../app/store.ts";

const popupNotificationsSlice = createSlice({
    name: 'popupNotifications',
    initialState: (): PopupNotificationsState => {
        return {
            messages: []
        }
    },
    reducers: {
        pushNotification(state: PopupNotificationsState, action: PayloadAction<NewNotification>) {
            state.messages.push({...action.payload, id: crypto.randomUUID()});
        },
        resetPopupNotifications(state) {
            state.messages = []
        }, 
        dismissNotification(state, action: PayloadAction<string>) {
            state.messages = state.messages.filter(m => m.id !== action.payload);                                                                                                                          
        }
    }
})

export const {resetPopupNotifications, pushNotification, dismissNotification} = popupNotificationsSlice.actions;

export default popupNotificationsSlice.reducer

export const selectPopupNotifications = (state: RootState) => state.popupNotifications.messages;