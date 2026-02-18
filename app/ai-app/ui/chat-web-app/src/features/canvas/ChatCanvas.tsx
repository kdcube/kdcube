import {RefObject, useMemo} from "react";
import useChatCanvasContext, {CanvasItemLink} from "./canvasContext.tsx";
import {useAppSelector} from "../../app/store.ts";
import {selectTurns} from "../chat/chatStateSlice.ts";
import {UnknownArtifact} from "../chat/chatTypes.ts";
import {
    getArtifactLinkComparator,
    getCanvasItemComponent,
    isCanvasArtifactType
} from "../extensions/canvasExtensions.ts";

interface ChatCanvasProps {
    ref?: RefObject<HTMLDivElement | null>;
}

const matchesLink = (link: CanvasItemLink | null,  artifact:UnknownArtifact) => {
    if (!link || !isCanvasArtifactType(artifact.artifactType)) return false;
    return getArtifactLinkComparator(artifact.artifactType)(link, artifact);
}

const ChatCanvas = ({ref}: ChatCanvasProps) => {
    const {itemLink} = useChatCanvasContext()

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
        return <CanvasComponent item={item} />
    }, [item])

    return useMemo(() => {
        return <div ref={ref} className={"h-full w-[35vw]"}>
            {content}
        </div>
    }, [content, ref])
}

export default ChatCanvas