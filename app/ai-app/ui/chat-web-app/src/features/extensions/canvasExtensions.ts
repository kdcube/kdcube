import {ComponentType} from "react";
import {UnknownArtifact} from "../chat/chatTypes.ts";
import {CanvasItemLink} from "../canvas/canvasContext.tsx";

export type ArtifactComponentProps = {
    item: UnknownArtifact
}

export type ArtifactLinkGenerator = (artifact: UnknownArtifact) => CanvasItemLink
export type ArtifactComponent = ComponentType<ArtifactComponentProps>
export type ArtifactLinkComparator = (link: CanvasItemLink, artifact: UnknownArtifact) => boolean

export interface CanvasExtension {
    component: ArtifactComponent
    linkGenerator: ArtifactLinkGenerator
    artifactLinkComparator: ArtifactLinkComparator
}

const canvasExtensions: Record<string, CanvasExtension> = {}

export const addCanvasItemExtension = (
    artifactType: string,
    component: ArtifactComponent,
    linkGenerator: ArtifactLinkGenerator,
    artifactLinkComparator:ArtifactLinkComparator
) => {
    canvasExtensions[artifactType] = {
        component,
        linkGenerator,
        artifactLinkComparator
    };
}

export const isCanvasArtifactType = (artifactType: string) => {
    return !!canvasExtensions[artifactType]
}

export const getCanvasArtifactTypes = ()=>{
    return Object.keys(canvasExtensions)
}

export const getCanvasItemComponent = (artifactType: string): ArtifactComponent => {
    return canvasExtensions[artifactType].component;
}

export const getCanvasItemLinkGenerator = (artifactType: string): ArtifactLinkGenerator => {
    return canvasExtensions[artifactType].linkGenerator;
}

export const getArtifactLinkComparator = (artifactType: string): ArtifactLinkComparator => {
    return canvasExtensions[artifactType].artifactLinkComparator;
}