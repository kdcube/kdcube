import {useCallback, useMemo} from "react";
import {Blocks, Loader, LogOut, Wifi, WifiOff} from "lucide-react";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {selectChatConnected, selectChatStayConnected} from "../../features/chat/chatStateSlice.ts";
import {logOut} from "../../features/auth/authMiddleware.ts";
import {useGetBundlesListQuery} from "../../features/bundles/bundlesAPI.ts";
import {selectProject, selectTenant} from "../../features/chat/chatSettingsSlice.ts";
import {selectCurrentBundle, setCurrentBundle} from "../../features/bundles/bundlesSlice.ts";
import IconContainer from "../IconContainer.tsx";

const ChatHeader = () => {
    const dispatch = useAppDispatch()
    const stayConnected = useAppSelector(selectChatStayConnected)
    const connected = useAppSelector(selectChatConnected)

    const connectionStatus = useMemo(() => {
        if (stayConnected && !connected) return {
            icon: <Loader size={14} className="animate-spin"/>,
            text: 'Connecting...',
            color: 'text-yellow-600 bg-yellow-50'
        };
        if (connected) return {icon: <Wifi size={14}/>, text: 'Connected', color: 'text-green-600 bg-green-50'};
        return {icon: <WifiOff size={14}/>, text: 'Disconnected', color: 'text-red-600 bg-red-50'};
    }, [stayConnected, connected]);

    const handleLogout = useCallback(() => {
        dispatch(logOut())
    }, [dispatch]);

    const tenant = useAppSelector(selectTenant)
    const project = useAppSelector(selectProject)

    const {data, isSuccess} = useGetBundlesListQuery({tenant, project})

    const currentBundle = useAppSelector(selectCurrentBundle)

    const bundlesSelector = useMemo(() => {
        if (isSuccess) {
            return <div className={"flex flex-row items-center p-1 border border-gray-200 rounded-md h-8 w-48 mr-auto ml-2"}>
                {/*<label htmlFor={"bundles_selector"} className={"text-sm"}>Bundle</label>*/}
                <IconContainer icon={Blocks} size={1.2} className={"stroke-[1.5px]"}/>
                <select id={"bundles_selector"} className={"text-sm w-full focus:outline-none truncate"}
                        value={currentBundle ?? undefined}
                        onChange={(event)=> {
                            dispatch(setCurrentBundle(event.target.value))
                        }}
                >
                    {Object.values(data.bundles).map(bundle => {
                        return <option value={bundle.id}
                                       key={bundle.id}>{bundle.name}{bundle.id === data.defaultBundle ? " (default)" : ""}</option>
                    })}
                </select>
            </div>
        }
        return null
    }, [currentBundle, data, dispatch, isSuccess])

    return useMemo(() => {
        return (
            <div className="bg-white border-b border-gray-200 px-4 py-2">
                <div className="flex items-center justify-between">
                    <div className="flex items-center">
                        <img src={"/img/logo.svg"} alt={"KDCube Logo"} className={"w-14 h-14"}/>
                        <div>
                            <h1 className="text-xl font-semibold text-gray-900">
                                KDCube
                            </h1>
                        </div>
                    </div>
                    {bundlesSelector}
                    <div className="flex items-center gap-2">


                        <div className={`flex items-center px-3 py-1 rounded-lg text-sm ${connectionStatus.color}`}>
                            {connectionStatus.icon}
                            <span className="ml-2 font-medium">{connectionStatus.text}</span>
                        </div>

                        {/*<button*/}
                        {/*    onClick={() => setShowKB(!showKB)}*/}
                        {/*    className="relative flex items-center px-3 py-2 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200"*/}
                        {/*    title="View KB"*/}
                        {/*>*/}
                        {/*    <Database size={16} className="mr-1"/><span className="text-sm">KB</span>*/}
                        {/*</button>*/}

                        {/*<button*/}
                        {/*    onClick={handleShowKbResults}*/}
                        {/*    className={`relative flex items-center px-3 py-2 rounded-lg transition-colors ${*/}
                        {/*        kbSearchHistory.length > 0 ? 'bg-blue-100 text-blue-700 hover:bg-blue-200' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'*/}
                        {/*    }`}*/}
                        {/*    title="View KB Search Results"*/}
                        {/*>*/}
                        {/*    <Search size={16} className="mr-1"/>*/}
                        {/*    <span className="text-sm">KB Search</span>*/}
                        {/*    {kbSearchHistory.length > 0 && (*/}
                        {/*        <span*/}
                        {/*            className="ml-1 text-xs bg-blue-200 text-blue-800 px-1 rounded">{kbSearchHistory.length}</span>*/}
                        {/*    )}*/}
                        {/*    {newKbSearchCount > 0 && (*/}
                        {/*        <span*/}
                        {/*            className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full animate-pulse"/>*/}
                        {/*    )}*/}
                        {/*</button>*/}


                        <button
                            onClick={handleLogout}
                            className="flex items-center px-3 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg"
                            title="Sign out"
                        >
                            <LogOut size={16} className="mr-1"/><span className="text-sm">Logout</span>
                        </button>
                    </div>
                </div>
            </div>
        )
    }, [bundlesSelector, connectionStatus.color, connectionStatus.icon, connectionStatus.text, handleLogout])
}

export default ChatHeader;