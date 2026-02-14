import {Action, configureStore, ThunkAction} from "@reduxjs/toolkit";
import {useDispatch, useSelector} from "react-redux";
import {suggestedQuestionsApiSlice} from "../features/suggestedQuestions/suggestedQuestions.ts";
import authSlice from "../features/auth/authSlice.ts";
import chatStateSlice from "../features/chat/chatStateSlice.ts";
import userProfileSlice from "../features/profile/profile.ts";
import {chatServiceMiddleware} from "../features/chat/chatServiceMiddleware.ts";
import {authMiddleware} from "../features/auth/authMiddleware.ts";
import conversationsSlice from "../features/conversations/conversationsSlice.ts";
import conversationsMiddleware from "../features/conversations/conversationsMiddleware.ts";
import {widgetPanelsApiSlice} from "../features/widgetPanels/widgetPanels.ts";
import chatSettingsSlice from "../features/chat/chatSettingsSlice.ts";

export const store = configureStore({
    reducer: {
        auth: authSlice,
        chatState: chatStateSlice,
        chatSettings: chatSettingsSlice,
        userProfile: userProfileSlice,
        conversations: conversationsSlice,
        [suggestedQuestionsApiSlice.reducerPath]: suggestedQuestionsApiSlice.reducer,
        [widgetPanelsApiSlice.reducerPath]: widgetPanelsApiSlice.reducer,
        // [userProfileApiSlice.reducerPath]: userProfileApiSlice.reducer,
        // [userProfileApiSlice.reducerPath]: userProfileApiSlice.reducer,
    },
    middleware: (getDefaultMiddleware) =>
        getDefaultMiddleware()
            .concat(
                suggestedQuestionsApiSlice.middleware,
                widgetPanelsApiSlice.middleware,
                chatServiceMiddleware("sse"),
                authMiddleware(),
                conversationsMiddleware()
            ),
})

export type AppStore = typeof store
export type AppDispatch = typeof store.dispatch
export type RootState = ReturnType<typeof store.getState>
export type AppThunk = ThunkAction<void, RootState, unknown, Action>
export const useAppDispatch = useDispatch.withTypes<AppDispatch>()
export const useAppSelector = useSelector.withTypes<RootState>()