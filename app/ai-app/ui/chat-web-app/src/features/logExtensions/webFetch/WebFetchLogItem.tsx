import {ChatLogComponentProps} from "../../extensions/logExtesnions.ts";
import {useMemo, useState} from "react";
import IconContainer from "../../../components/IconContainer.tsx";
import {WebFetchArtifact, WebFetchArtifactType, WebFetchDataItem} from "./types.ts";
import {BanknoteX, Check, Download, Ellipsis, GlobeOff} from "lucide-react";
import IconLoader from "../../../components/IconLoader.tsx";

const WebFetchLogItem = ({item}: ChatLogComponentProps) => {
    const maxVisibleLinks = 3;

    if (item.artifactType !== WebFetchArtifactType) {
        throw new Error("not a WebFetchArtifact");
    }

    const webSearchItem = item as WebFetchArtifact;

    const [expanded, setExpanded] = useState<boolean>(false)

    const expandable = useMemo(() => {
        return webSearchItem.content.items.length > maxVisibleLinks
    }, [webSearchItem.content.items, maxVisibleLinks])

    const visibleLinks = useMemo(() => {
        return expanded || !expandable ? webSearchItem.content.items : webSearchItem.content.items.slice(0, maxVisibleLinks + 1)
    }, [expanded, expandable, webSearchItem.content.items])

    return useMemo(() => {
        const getIcon = (item: WebFetchDataItem) => {
            switch (item.status) {
                case "success":
                    return <IconContainer icon={Check} size={1} className={"text-green-900"}/>
                case "paywall":
                    return <IconContainer icon={BanknoteX} size={1} className={"text-yellow-800"}/>
                default:
                    return <IconContainer icon={GlobeOff} size={1} className={"text-red-800"}/>
            }
        }
        return <div
            id={`weFetch_${webSearchItem.content.name}`}
            className={`relative w-full text-left p-2 min-w-0 flex flex-row rounded-lg mb-2 border border-gray-200`}>
            <div className={"flex flex-row w-full duration-200 transition-all"}>
                <IconContainer icon={Download} size={1.5} className={"mr-0.5"}/>
                <div className={"flex-1 min-w-0 mb-4"}>
                    <div className={"flex flex-row w-full min-w-0"}>
                        {webSearchItem.content.title ?? webSearchItem.content.name}
                    </div>
                    {visibleLinks.length > 0 &&
                        <div className={"text-sm border-gray-200 border p-2 rounded-md mt-1 w-full min-w-0"}>
                            {visibleLinks.map((link, i) => {
                                return <div className={"flex flex-row w-full min-w-0 items-center"} key={i}>
                                    <div className={"cursor-pointer min-w-0"}>
                                        <a
                                            key={i}
                                            href={link.url}
                                            target={"_blank"}
                                            className={"underline flex flex-row items-center gap-1"}
                                        >
                                            <IconLoader url={link.favicon} size={1}/>
                                            <span className={"truncate"}>{link.url}</span>
                                        </a>
                                    </div>
                                    <div className={"ml-2 mr-auto"}>{getIcon(link)}</div>
                                </div>
                            })}
                            {expandable && (expanded ? <div>

                            </div> : <button
                                className={"cursor-pointer hover:text-black mt-1 flex flex-row items-center gap-1"}
                                onClick={() => {
                                    setExpanded(true)
                                }}
                            >
                                <IconContainer icon={Ellipsis} size={1}/>
                                {webSearchItem.content.items.length - visibleLinks.length} more
                            </button>)}
                        </div>}
                </div>
            </div>
        </div>
    }, [webSearchItem.content.name, webSearchItem.content.title, webSearchItem.content.items.length, visibleLinks, expandable, expanded])
}

export default WebFetchLogItem;