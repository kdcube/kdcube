import {createContext, useContext} from "react";

export type SidePanel =
    "conversations"
    | "artifacts"
    | "economics"
    | "ai_bundles"
    | "gateway"
    | "conv_browser"
    | "redis_browser"
    | "storage"
    | "connections"
    | "economic_usage"
    | "bundle_widget"
    | "debug"
    | string
    | null

export interface SidePanelContextValue {
    panelId: SidePanel;
    setPanelId: (widgetId: SidePanel) => void;
}

const SidePanelContext = createContext<SidePanelContextValue>({
    panelId: null,
    setPanelId: () => {
        throw new Error("not implemented");
    },
})

export const useSidePanelContext = () => useContext(SidePanelContext)

export default SidePanelContext