import {Artifact, SubsystemEventData, TurnEvent} from "../../chat/chatTypes.ts";

export interface WebSearchLink {
    url: string,
    title?: string | null,
    body?: string | null,
    favicon?: string | null,
    provider: string;
    weightedScore: number;
}

export interface WebSearchData {
    name: string;
    searchId: string;
    title?: string | null;
    items: WebSearchLink[];
    objective?: string;
    queries?: string[];
    reportContent?: string | null;
}

export const WebSearchArtifactType = "web_search_results"

export interface WebSearchArtifact extends Artifact<WebSearchData> {
    artifactType: typeof WebSearchArtifactType;
}

export interface WebSearchSubsystemEventData extends SubsystemEventData {
    searchId: string;
}

export const WebSearchFilteredResultsSubsystemEventDataSubtype = "web_search.filtered_results"

export interface WebSearchFilteredResultsSubsystemEventData extends WebSearchSubsystemEventData {
    subtype: typeof WebSearchFilteredResultsSubsystemEventDataSubtype
}

export const WebSearchHTMLViewSubsystemEventDataSubtype = "web_search.html_view"

export interface WebSearchHTMLViewSubsystemEventData extends WebSearchSubsystemEventData {
    subtype: typeof WebSearchHTMLViewSubsystemEventDataSubtype
}

export const WebSearchEventSubtypes = [WebSearchFilteredResultsSubsystemEventDataSubtype, WebSearchHTMLViewSubsystemEventDataSubtype]
export type WebSearchMetaEventData = WebSearchFilteredResultsSubsystemEventData | WebSearchHTMLViewSubsystemEventData
export type WebSearchEvent = TurnEvent<WebSearchMetaEventData>