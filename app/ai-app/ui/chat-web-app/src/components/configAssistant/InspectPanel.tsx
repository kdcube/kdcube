/*
 * SPDX-License-Identifier: MIT
 * InspectPanel — right-side panel of the Configuration Assistant page.
 * Hosts six tabs: Graph, Concept, Footprint, Source, Config drafts, My Bundle.
 * Tab content is driven by configAssistantSlice.inspect.
 */
import {useCallback} from "react";
import {Network, BookOpen, Code2, FileCode, FileText, Package} from "lucide-react";

import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {
    selectConfigAssistantInspect,
    setInspectTab,
} from "../../features/configAssistant/configAssistantSlice.ts";
import GraphTab from "./tabs/GraphTab.tsx";
import ConceptTab from "./tabs/ConceptTab.tsx";
import FootprintTab from "./tabs/FootprintTab.tsx";
import SourceTab from "./tabs/SourceTab.tsx";
import ConfigDraftsTab from "./tabs/ConfigDraftsTab.tsx";
import MyBundleTab from "./tabs/MyBundleTab.tsx";

type TabId = "graph" | "concept" | "footprint" | "source" | "config" | "my_bundle";

const TABS: ReadonlyArray<{id: TabId; label: string; icon: typeof Network}> = [
    {id: "graph", label: "Graph", icon: Network},
    {id: "concept", label: "Concept", icon: BookOpen},
    {id: "footprint", label: "Footprint", icon: Code2},
    {id: "source", label: "Source", icon: FileCode},
    {id: "config", label: "Config", icon: FileText},
    {id: "my_bundle", label: "My Bundle", icon: Package},
];

function InspectPanel() {
    const dispatch = useAppDispatch();
    const inspect = useAppSelector(selectConfigAssistantInspect);
    const activeTab = inspect.activeTab;

    const onTabClick = useCallback(
        (id: TabId) => () => {
            dispatch(setInspectTab(id));
        },
        [dispatch],
    );

    return (
        <div className="flex flex-col h-full w-[420px] min-w-[360px] max-w-[520px] border-l border-slate-200 bg-white">
            <div className="flex flex-row items-stretch border-b border-slate-200 bg-slate-50">
                {TABS.map(({id, label, icon: Icon}) => {
                    const isActive = activeTab === id;
                    return (
                        <button
                            key={id}
                            type="button"
                            onClick={onTabClick(id)}
                            className={[
                                "flex-1 flex flex-col items-center justify-center gap-0.5 py-2 px-1 text-xs font-medium transition-colors",
                                isActive
                                    ? "bg-white text-slate-900 border-b-2 border-blue-500"
                                    : "text-slate-500 hover:text-slate-700 hover:bg-white/60 border-b-2 border-transparent",
                            ].join(" ")}
                            title={label}
                        >
                            <Icon size={16} strokeWidth={2}/>
                            <span className="leading-tight">{label}</span>
                        </button>
                    );
                })}
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto">
                {activeTab === "graph" && <GraphTab/>}
                {activeTab === "concept" && <ConceptTab/>}
                {activeTab === "footprint" && <FootprintTab/>}
                {activeTab === "source" && <SourceTab/>}
                {activeTab === "config" && <ConfigDraftsTab/>}
                {activeTab === "my_bundle" && <MyBundleTab/>}
            </div>
        </div>
    );
}

export default InspectPanel;
