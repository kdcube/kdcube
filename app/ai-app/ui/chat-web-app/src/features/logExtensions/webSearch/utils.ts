import {CanvasItemLink} from "../../canvas/canvasContext.tsx";
import {WebSearchArtifact, WebSearchArtifactType} from "./types.ts";
import {UnknownArtifact} from "../../chat/chatTypes.ts";
import {copyMarkdownToClipboard, saveStringAsFile} from "../../../components/Clipboard.ts";
import {RefObject} from "react";

export const isWebSearchArtifactLink = (link: CanvasItemLink | null) => {
    return link?.itemType === WebSearchArtifactType
}

export const matchesWebSearchWithId = (link: CanvasItemLink | null, searchId: string) => {
    return searchId === link!.searchId
}

export const matchesWebSearchArtifact = (link: CanvasItemLink | null, item: UnknownArtifact) => {
    if (link?.itemType !== WebSearchArtifactType) return false;
    return matchesWebSearchWithId(link, (item as WebSearchArtifact).content.searchId)
}

export const getWebSearchArtifactLink = (artifact: UnknownArtifact): CanvasItemLink => {
    if (artifact.artifactType !== WebSearchArtifactType) {
        throw new Error("not a WebSearchArtifactType");
    }
    return {
        itemType: WebSearchArtifactType,
        searchId: (artifact as WebSearchArtifact).content.searchId,
    }
}

export const getWebSearchArtifactTitle = (artifact: UnknownArtifact): string => {
    if (artifact.artifactType !== WebSearchArtifactType) {
        throw new Error("not a WebSearchArtifactType");
    }
    return (artifact as WebSearchArtifact).content.title ?? (artifact as WebSearchArtifact).content.name
}

export const copyWebSearchArtifact = (artifact: UnknownArtifact, contentRef?: RefObject<HTMLDivElement | null> | null): void => {
    if (artifact.artifactType !== WebSearchArtifactType) {
        throw new Error("not a WebSearchArtifactType");
    }
    const searchItem = artifact as WebSearchArtifact
    if (!searchItem.content.reportContent) {
        console.warn("attempted to copy nonexistent report");
        return;
    }
    copyMarkdownToClipboard(searchItem.content.reportContent as string, contentRef && contentRef.current ? contentRef.current.innerHTML : undefined).catch(console.error);
}

export const saveWebSearchArtifact = (artifact: UnknownArtifact): void => {
    if (artifact.artifactType !== WebSearchArtifactType) {
        throw new Error("not a WebSearchArtifactType");
    }
    const searchItem = artifact as WebSearchArtifact
    if (!searchItem.content.reportContent) {
        console.warn("attempted to save nonexistent report");
        return;
    }

    saveStringAsFile(searchItem.content.reportContent as string, `${searchItem.content.name}.md`).catch(console.error);
}