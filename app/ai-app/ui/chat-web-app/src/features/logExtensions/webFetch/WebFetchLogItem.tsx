import {ChatLogComponentProps} from "../../extensions/logExtesnions.ts";
import {useMemo, useState} from "react";
import IconContainer from "../../../components/IconContainer.tsx";
import {WebFetchArtifact, WebFetchArtifactType, WebFetchDataItem} from "./types.ts";
import {Download, Ellipsis} from "lucide-react";
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
        const getStatus = (item: WebFetchDataItem) => {
            switch (item.status) {
                case null:
                case undefined:
                    return null
                case "success":
                    return <span className={`text-green-700 text-sm`}> - Success</span>
                case "paywall":
                    return <div className={`text-red-800 text-sm`}> - Paywall</div>
                case "timeout":
                    return <div className={`text-red-800 text-sm`}> - Timeout</div>
                case "error":
                default:
                    return <div className={`text-red-800 text-sm`}> - Failed</div>
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
                                    <div className={"ml-1 mr-auto"}>{getStatus(link)}</div>
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