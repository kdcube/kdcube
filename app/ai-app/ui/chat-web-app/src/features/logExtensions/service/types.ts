import {Artifact} from "../../chat/chatTypes.ts";

export interface ServiceErrorArtifactData {
    message: string;
}

export const ServiceErrorArtifactType = "service_error"

export interface ServiceErrorArtifact extends Artifact<ServiceErrorArtifactData> {
    artifactType: typeof ServiceErrorArtifactType
}