/*
 * SPDX-License-Identifier: MIT
 * Copyright (c) 2025 Elena Viter
 */

import {BrowserRouter as Router, Route, Routes} from "react-router-dom"
import NotFoundPage from "./components/notfound/NotFoundPage.tsx";
import {ReactNode, useCallback, useMemo} from "react";
import Dummy from "./components/chat/Dummy.tsx";
import AuthCallback from "./features/auth/AuthCallback.tsx";
import WithAuthRequired from "./features/auth/WithAuthRequired.tsx";
import ChatPage from "./components/chat/ChatPage.tsx";
import {useAppSelector} from "./app/store.ts";
import {selectChatPath, selectRoutesPrefix} from "./features/chat/chatSettingsSlice.ts";


function AppRouter() {
    const routePrefix = useAppSelector(selectRoutesPrefix)
    const chatPagePath = useAppSelector(selectChatPath)

    const withAuthRequired = useCallback((children: ReactNode | ReactNode[]) => {
        return <WithAuthRequired>{children}</WithAuthRequired>
    }, [])

    const chatPage = useMemo(() => {
        return withAuthRequired(<ChatPage/>)
    }, [withAuthRequired])

    return useMemo(() => {
        return <Router>
            <Routes>
                <Route path={`${routePrefix}/callback`} element={<AuthCallback/>}/>
                <Route path={chatPagePath} element={chatPage}/>
                <Route path={`${chatPagePath}/:conversationID`} element={chatPage}/>
                <Route path={`${routePrefix}/dummy`} element={withAuthRequired(<Dummy/>)}/>
                <Route path='*' element={<NotFoundPage/>}/>
            </Routes>
        </Router>
    }, [routePrefix, chatPagePath, chatPage, withAuthRequired])
}

export default AppRouter
