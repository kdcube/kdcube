import {Artifact} from "../../chat/chatTypes.ts";

export interface CanvasItemData {
    name: string;
    description?: string | null; //todo: do we still have it somewhere?
    content: unknown;
    contentType: string;
    subType?: "webSearch" | string | null; //todo: do we still have it somewhere?
    title?: string | null;
}

export const CanvasArtifactType = "canvas";

export interface CanvasArtifact extends Artifact<CanvasItemData> {
    artifactType: typeof CanvasArtifactType;
    complete?: boolean;
    canCopy: true;
    canSave: true;
}