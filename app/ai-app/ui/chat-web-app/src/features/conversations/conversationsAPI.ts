import {appendDefaultCredentialsHeader} from "../../app/api/utils.ts";
import {ConversationDescriptorDTO, ConversationDTO} from "./conversationsTypes.ts";
import {chatAPIBasePath} from "../../BuildConfig.ts";

export const getConversations = async (tenant: string, project: string) => {
    const headers = appendDefaultCredentialsHeader({"Content-Type": "application/json"});
    const res = await fetch(`${chatAPIBasePath}/api/cb/conversations/${tenant}/${project}`, {
        method: "GET",
        headers
    });
    if (!res.ok) throw new Error(`Failed to get conversations (${res.status})`);
    const data = await res.json();
    return data.items as ConversationDescriptorDTO[];
};

export const getConversationDetails = async (tenant: string, project: string, conversationId: string) => {
    const headers = appendDefaultCredentialsHeader({"Content-Type": "application/json"});
    const res = await fetch(`${chatAPIBasePath}/api/cb/conversations/${tenant}/${project}/${conversationId}/details`, {
        method: "GET",
        headers
    });
    if (!res.ok) throw new Error("Failed to get conversation details");
    return await res.json();
};

export const fetchConversation = async (
    tenant: string,
    project: string,
    conversationId: string,
    materialize: boolean = true,
    turnIds?: string[]
) => {
    const headers = appendDefaultCredentialsHeader({"Content-Type": "application/json"});
    const body: Record<string, unknown> = {materialize};
    if (turnIds) body["turn_ids"] = turnIds;
    const res = await fetch(
        `${chatAPIBasePath}/api/cb/conversations/${tenant}/${project}/${conversationId}/fetch`,
        {method: "POST", headers, body: JSON.stringify(body)}
    );
    if (!res.ok) throw new Error("Failed to fetch conversation");
    return await res.json() as ConversationDTO;
};

export const deleteConversation = async (tenant: string, project: string, conversationId: string) => {
    const headers = appendDefaultCredentialsHeader({"Content-Type": "application/json"});
    const res = await fetch(`${chatAPIBasePath}/api/cb/conversations/${tenant}/${project}/${conversationId}`, {
        method: "DELETE", headers
    });
    if (!res.ok) throw new Error("Failed to delete conversation");
    return await res.json();
};