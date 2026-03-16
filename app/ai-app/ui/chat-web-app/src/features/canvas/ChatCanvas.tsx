import {RefObject, useMemo, useRef} from "react";
import useChatCanvasContext, {CanvasItemLink} from "./canvasContext.tsx";
import {useAppSelector} from "../../app/store.ts";
import {selectTurns} from "../chat/chatStateSlice.ts";
import {UnknownArtifact} from "../chat/chatTypes.ts";
import {
    getArtifactCopyHandler,
    getArtifactLinkComparator, getArtifactSaveHandler,
    getArtifactTitleGenerator,
    getCanvasItemComponent,
    isCanvasArtifactType
} from "../extensions/canvasExtensions.ts";
import IconContainer from "../../components/IconContainer.tsx";
import {Copy, Save, X} from "lucide-react";

interface ChatCanvasProps {
    ref?: RefObject<HTMLDivElement | null>;
}

const matchesLink = (link: CanvasItemLink | null, artifact: UnknownArtifact) => {
    if (!link || !isCanvasArtifactType(artifact.artifactType)) return false;
    return getArtifactLinkComparator(artifact.artifactType)(link, artifact);
}

const ChatCanvas = ({ref}: ChatCanvasProps) => {

    const {itemLink, showItem} = useChatCanvasContext()
    const contentRef = useRef<HTMLDivElement>(null);

    const turns = useAppSelector(selectTurns)
    const artifacts = useMemo(() => {
        return Object.values(turns).reduce((acc, cur) => {
            acc = acc.concat(cur.artifacts)
            return acc
        }, [] as UnknownArtifact[])
    }, [turns])

    const item = useMemo(() => {
        return artifacts.find(a => matchesLink(itemLink, a))
    }, [artifacts, itemLink])

    const content = useMemo(() => {
        if (!item) return null
        const CanvasComponent = getCanvasItemComponent(item.artifactType)
        return <CanvasComponent item={item} contentRef={contentRef}/>
    }, [item])

    const header = useMemo(() => {
        if (!item) return null
        const title = getArtifactTitleGenerator(item.artifactType)(item)
        const copyHandler = item.canCopy && getArtifactCopyHandler(item.artifactType)
        const saveHandler = item.canSave && getArtifactSaveHandler(item.artifactType)

        return <div className={"flex flex-row items-center"}>
            <h3 className={"text-xl pl-1"}>{title}</h3>
            <div className={"ml-2 flex flex-row flex-1 gap-0.5"}>
                {copyHandler &&
                    <button
                        className={"cursor-pointer text-gray-800 hover:text-black"}
                        onClick={() => {
                            copyHandler(item)
                        }}
                    ><IconContainer icon={Copy} size={1.25}/>
                    </button>}
                {saveHandler &&
                    <button
                        className={"cursor-pointer text-gray-800 hover:text-black"}
                        onClick={() => {
                            saveHandler(item)
                        }}
                    ><IconContainer icon={Save} size={1.25}/>
                    </button>}
                <button
                    className={"cursor-pointer ml-auto text-gray-800 hover:text-black"}
                    onClick={() => {
                        showItem(null)
                    }}
                ><IconContainer icon={X} size={1.25}/>
                </button>
            </div>
        </div>
    }, [item, showItem])

    return useMemo(() => {
        return <div ref={ref} className={"h-full w-[35vw] flex flex-col"}>
            <div className={"p-2 border-b border-gray-200 bg-gray-50"}>
                {header}
            </div>
            {content}
        </div>
    }, [content, header, ref])
}

export default ChatCanvas