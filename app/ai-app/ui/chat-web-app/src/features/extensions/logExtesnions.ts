import {ComponentType} from "react";
import {UnknownArtifact} from "../chat/chatTypes.ts";

export type ChatLogComponentProps = {
    item: UnknownArtifact
    historical?: boolean|null
}

export type ChatLogComponent = ComponentType<ChatLogComponentProps>

export interface ChatLogExtension {
    component: ChatLogComponent
}

const chatLogExtensions: Record<string, ChatLogExtension | undefined> = {}

export const addChatLogExtension = (
    artifactType: string,
    component: ChatLogComponent,
) => {
    chatLogExtensions[artifactType] = {
        component,
    };
}

export const isChatLogType = (artifactType: string) => {
    return !!chatLogExtensions[artifactType]
}

export const getChatLogTypes = ()=>{
    return Object.keys(chatLogExtensions)
}

export const getChatLogComponent = (artifactType: string): ChatLogComponent | undefined => {
    return chatLogExtensions[artifactType]?.component;
}