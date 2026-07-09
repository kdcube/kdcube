import {useEffect, useMemo, useState} from "react";
import {Blocks} from "lucide-react";
import {useAppSelector} from "../../app/store.ts";
import {selectProject, selectTenant} from "../chat/chatSettingsSlice.ts";
import {getLucideIconComponent} from "../../components/DynamicLucideIcon/utils.ts";
import IconContainer from "../../components/IconContainer.tsx";
import {useGetBundleWidgets} from "./widgetReducer.tsx";

// The automatic scene: the main surface for an app without its own main
// view. The app's visible widgets become summonable chips; the picked
// widget fills the stage. Widgets stay mounted once opened so switching
// chips keeps their state (the workspace-scene convention). An app that
// declares the default chat surface serves the SDK chat widget under the
// reserved `chat` alias — it summons first and opens on arrival.

const CHAT_WIDGET_ALIAS = "chat";

function widgetTitle(alias: string): string {
    return alias
        .replace(/[_-]+/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
}

const AppScene = () => {
    const tenant = useAppSelector(selectTenant);
    const project = useAppSelector(selectProject);
    const {currentBundleId, currentBundleData, widgets: visibleWidgets} = useGetBundleWidgets();

    const widgets = useMemo(() => {
        const chat = visibleWidgets.filter((widget) => widget.alias === CHAT_WIDGET_ALIAS);
        const rest = visibleWidgets.filter((widget) => widget.alias !== CHAT_WIDGET_ALIAS);
        return [...chat, ...rest];
    }, [visibleWidgets]);

    const [activeAlias, setActiveAlias] = useState<string | null>(null);
    const [openedAliases, setOpenedAliases] = useState<string[]>([]);

    // New app selected: reset to its first widget (the chat, when declared).
    useEffect(() => {
        const first = widgets.length ? widgets[0].alias : null;
        setActiveAlias(first);
        setOpenedAliases(first ? [first] : []);
    }, [currentBundleId, widgets]);

    const summon = (alias: string) => {
        setActiveAlias(alias);
        setOpenedAliases((current) => (current.includes(alias) ? current : [...current, alias]));
    };

    const appName = currentBundleData?.name || currentBundleId || "";

    return useMemo(() => {
        return <div className={"flex-1 flex flex-col h-full min-h-0 bg-[#EEF5F5]"}>
            <div className={"flex flex-row items-center gap-2 px-4 py-2 border-b border-[#E6F1F0] bg-white"}>
                <span className={"text-[10.5px] font-bold tracking-[0.1em] uppercase text-[#7A99B0]"}>
                    {appName}
                </span>
                <div className={"flex flex-row items-center gap-1.5 flex-wrap"}>
                    {widgets.map((widget) => {
                        const Icon = getLucideIconComponent(widget.icon?.lucide ?? null, Blocks);
                        const isActive = widget.alias === activeAlias;
                        return <button
                            key={widget.alias}
                            type={"button"}
                            title={widgetTitle(widget.alias)}
                            onClick={() => summon(widget.alias)}
                            className={`h-8 px-3 rounded-full border text-[12px] font-semibold flex items-center gap-1.5 transition-colors ${
                                isActive
                                    ? "border-[#01BEB2] bg-[rgba(1,190,178,0.08)] text-[#009C92]"
                                    : "border-[#D8ECEB] bg-white text-[#3A5672] hover:bg-[#F6FAFA]"
                            }`}
                        >
                            <IconContainer icon={Icon} size={1} className={"stroke-[1.7px]"}/>
                            <span>{widgetTitle(widget.alias)}</span>
                        </button>
                    })}
                </div>
            </div>
            <div className={"flex-1 relative min-h-0"}>
                {openedAliases.map((alias) => {
                    const src = currentBundleId
                        ? `/api/integrations/bundles/${encodeURIComponent(tenant)}/${encodeURIComponent(project)}/${encodeURIComponent(currentBundleId)}/widgets/${encodeURIComponent(alias)}`
                        : null;
                    if (!src) return null;
                    return <iframe
                        key={`${currentBundleId}:${alias}`}
                        src={src}
                        title={`${currentBundleId}:${alias}`}
                        className={"w-full h-full absolute left-0 top-0 border-0 bg-[#EEF5F5]"}
                        style={{visibility: alias === activeAlias ? "visible" : "hidden"}}
                    />
                })}
                {widgets.length === 0 &&
                    <div className={"w-full h-full flex items-center justify-center"}>
                        <div className={"text-center"}>
                            <p className={"text-[13px] text-[#3A5672] font-semibold"}>{appName}</p>
                            <p className={"text-[12.5px] text-[#7A99B0] mt-1"}>
                                This app serves in the background; there is nothing to open here.
                            </p>
                        </div>
                    </div>
                }
            </div>
        </div>
    }, [activeAlias, appName, currentBundleId, openedAliases, project, tenant, widgets])
}

export default AppScene;
