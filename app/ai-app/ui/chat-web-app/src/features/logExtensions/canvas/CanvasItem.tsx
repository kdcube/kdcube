import hljs from "highlight.js";
import {useMemo, useState} from "react";
import ReactMarkdown from "react-markdown";
import {
    markdownComponentsTight,
    rehypePlugins,
    remarkPlugins
} from "../../../components/chat/ChatInterface/markdownRenderUtils.tsx";
import {closeUpMarkdown} from "../../../components/WordStreamingEffects.tsx";
import MermaidDiagram from "../../../components/MermaidDiagram.tsx";
import {appendCodeMarkdown, cleanupCode} from "../../canvas/utils.ts";
import {CanvasArtifact, CanvasArtifactType} from "./types.ts";
import {ArtifactComponentProps} from "../../extensions/canvasExtensions.ts";

const getCanvasContentType = (format: string | null | undefined) => {
    if (!format) {
        return null;
    }
    format = format.toLowerCase();

    if (["markdown", "mermaid", "csv"].includes(format)) return format;
    if (hljs.listLanguages().includes(format)) return "code";
    if (format === "html") return "srcdoc";
    return null
}

const CanvasItem = ({item}: ArtifactComponentProps) => {
    if (item.artifactType !== CanvasArtifactType) {
        throw new Error("not a CanvasArtifactType")
    }

    const canvasItem = item as CanvasArtifact;

    const [showItemSource, setShowItemSource] = useState<boolean>(false)

    const contentType = useMemo(() => {
        return canvasItem ? getCanvasContentType(canvasItem.content.contentType) : null
    }, [canvasItem])

    const itemCompleted = useMemo(() => {
        return !!canvasItem?.complete
    }, [canvasItem])

    const showSourceSwitch = useMemo(() => {
        return itemCompleted && contentType && ["srcdoc", "mermaid", "csv"].includes(contentType)
    }, [itemCompleted, contentType])

    const itemSource = useMemo(() => {
        if (!canvasItem) return null;
        return String(canvasItem.content.content)
    }, [canvasItem])

    const itemRender = useMemo(() => {
        if (!canvasItem) return null;
        switch (contentType) {
            case "markdown":
                return <ReactMarkdown
                    remarkPlugins={remarkPlugins}
                    rehypePlugins={rehypePlugins}
                    components={markdownComponentsTight}
                    skipHtml={false}
                >
                    {closeUpMarkdown(canvasItem.content.content as string)}
                </ReactMarkdown>
            case "mermaid":
                return <MermaidDiagram chart={canvasItem.content.content as string}/>
            case "code":
                return <ReactMarkdown
                    remarkPlugins={remarkPlugins}
                    rehypePlugins={rehypePlugins}
                    components={markdownComponentsTight}
                    skipHtml={false}
                >
                    {appendCodeMarkdown(cleanupCode(canvasItem.content.content as string), canvasItem.content.contentType)}
                </ReactMarkdown>
            default:
                return <div>not supported</div>;
        }

    }, [contentType, canvasItem])

    return useMemo(() => {
        return <div className={"p-2 border-gray-200 border-l-1 bg-white h-full w-full overflow-y-auto"}>
            {showItemSource || !itemCompleted || !contentType ? itemSource : itemRender}
        </div>
    }, [contentType, itemCompleted, itemRender, itemSource, showItemSource])
}

export default CanvasItem