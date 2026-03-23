import {createApi, fetchBaseQuery} from "@reduxjs/toolkit/query/react";
import {
    AIBundlesResponse,
    ConversationsBrowserResponse,
    EconomicsResponse,
    EconomicUsageResponse,
    GatewayResponse,
    RedisBrowserResponse
} from "./types.ts";
import {appendDefaultCredentialsHeader} from "../../app/api/utils.ts";
import {ChatScope} from "../chat/chatTypes.ts";

const EconomicsTag = "economics"
const AIBundlesTag = "ai_bundles"
const GatewayTag = "gateway"
const ConversationBrowserTag = "conversation_browser"
const RedisBrowserTag = "redis_browser"
const EconomicUsageTag = "economic_usage"

export type GetWidgetParams = ChatScope

export const widgetPanelsApiSlice = createApi({
    reducerPath: 'widgetPanels',
    baseQuery: fetchBaseQuery({
        prepareHeaders(headers) {
            return appendDefaultCredentialsHeader(headers) as Headers;
        }
    }),
    tagTypes: [EconomicsTag, AIBundlesTag, GatewayTag, ConversationBrowserTag, RedisBrowserTag, EconomicUsageTag],
    endpoints: builder => ({
        getEconomicsWidget: builder.query<string, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}: GetWidgetParams) => {
                return {
                    url: `/api/integrations/bundles/${tenant}/${project}/operations/control_plane`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}"
                }
            },
            transformResponse(res: EconomicsResponse) {
                return res.control_plane[0]
            },
            providesTags: [EconomicsTag],
        }),
        getAIBundlesWidget: builder.query<string, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}: GetWidgetParams) => {
                return {
                    url: `/api/integrations/bundles/${tenant}/${project}/operations/ai_bundles`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}"
                }
            },
            transformResponse(res: AIBundlesResponse) {
                return res.ai_bundles[0]
            },
            providesTags: [AIBundlesTag],
        }),
        getGatewayWidget: builder.query<string, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}: GetWidgetParams) => {
                return {
                    url: `/api/integrations/bundles/${tenant}/${project}/operations/svc_gateway`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}"
                }
            },
            transformResponse(res: GatewayResponse) {
                return res.svc_gateway[0]
            },
            providesTags: [GatewayTag],
        }),
        getConversationBrowserWidget: builder.query<string, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}: GetWidgetParams) => {
                return {
                    url: `/api/integrations/bundles/${tenant}/${project}/operations/conversation_browser`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}"
                }
            },
            transformResponse(res: ConversationsBrowserResponse) {
                return res.conversation_browser[0]
            },
            providesTags: [ConversationBrowserTag],
        }),
        getRedisBrowserWidget: builder.query<string, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}: GetWidgetParams) => {
                return {
                    url: `/api/integrations/bundles/${tenant}/${project}/operations/redis_browser`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}"
                }
            },
            transformResponse(res: RedisBrowserResponse) {
                return res.redis_browser[0]
            },
            providesTags: [RedisBrowserTag],
        }),
        getEconomicUsageWidget: builder.query<string, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}: GetWidgetParams) => {
                return {
                    url: `/api/integrations/bundles/${tenant}/${project}/operations/economic_usage`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}"
                }
            },
            transformResponse(res: EconomicUsageResponse) {
                return res.economic_usage[0]
            },
            providesTags: [EconomicUsageTag],
        }),
    })
})

export const {
    useGetEconomicsWidgetQuery, useLazyGetEconomicsWidgetQuery,
    useGetAIBundlesWidgetQuery, useLazyGetAIBundlesWidgetQuery,
    useGetGatewayWidgetQuery, useLazyGetGatewayWidgetQuery,
    useGetConversationBrowserWidgetQuery, useLazyGetConversationBrowserWidgetQuery,
    useGetRedisBrowserWidgetQuery, useLazyGetRedisBrowserWidgetQuery,
    useGetEconomicUsageWidgetQuery, useLazyGetEconomicUsageWidgetQuery,
} = widgetPanelsApiSlice
