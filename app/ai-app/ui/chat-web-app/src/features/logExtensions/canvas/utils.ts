import {CanvasItemLink} from "../../canvas/canvasContext.tsx";
import {CanvasArtifact, CanvasArtifactType} from "./types.ts";
import {UnknownArtifact} from "../../chat/chatTypes.ts";
import {RefObject} from "react";
import {copyMarkdownToClipboard, saveStringAsFile} from "../../../components/Clipboard.ts";

export const isCanvasItemLink = (link: CanvasItemLink | null) => {
    return link?.itemType === CanvasArtifactType
}

export const matchesCanvasWithName = (link: CanvasItemLink | null, name: string) => {
    return name === link?.name
}

export const matchesCanvasArtifact = (link: CanvasItemLink | null, item: UnknownArtifact) => {
    if (link?.itemType !== CanvasArtifactType) return false;
    return matchesCanvasWithName(link, (item as CanvasArtifact).content.name)
}

export const getCanvasArtifactLink = (artifact: UnknownArtifact): CanvasItemLink => {
    if (artifact.artifactType !== CanvasArtifactType) {
        throw new Error("not a CanvasArtifactType");
    }
    return {
        itemType: CanvasArtifactType,
        name: (artifact as CanvasArtifact).content.name,
    }
}

export const getCanvasArtifactTitle = (artifact: UnknownArtifact): string => {
    if (artifact.artifactType !== CanvasArtifactType) {
        throw new Error("not a CanvasArtifactType");
    }
    return (artifact as CanvasArtifact).content.title ?? (artifact as CanvasArtifact).content.name
}

export const copyCanvasArtifact = (artifact: UnknownArtifact, contentRef?: RefObject<HTMLDivElement | null> | null): void => {
    if (artifact.artifactType !== CanvasArtifactType) {
        throw new Error("not a CanvasArtifactType");
    }
    copyMarkdownToClipboard((artifact as CanvasArtifact).content.content as string, contentRef && contentRef.current ? contentRef.current.innerHTML : undefined).catch(console.error);
}

export const saveCanvasArtifact = (artifact: UnknownArtifact): void => {
    if (artifact.artifactType !== CanvasArtifactType) {
        throw new Error("not a CanvasArtifactType");
    }
    const canvasItem = artifact as CanvasArtifact
    saveStringAsFile(canvasItem.content.content as string, `${canvasItem.content.name}.txt`).catch(console.error);
}