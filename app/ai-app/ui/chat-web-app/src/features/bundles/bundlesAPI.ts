import {createApi, fetchBaseQuery} from "@reduxjs/toolkit/query/react";
import {appendDefaultHeaders} from "../../app/api/utils.ts";
import {ChatScope} from "../chat/chatTypes.ts";
import {BundlesInfo, BundlesResponse} from "./types.ts";

type GetBundlesListRequest = ChatScope

interface GetBundleUIRequest extends ChatScope {
    bundleId: string
}

export const bundlesApiSlice = createApi({
    reducerPath: 'bundlesAPI',
    baseQuery: fetchBaseQuery({
        prepareHeaders(headers) {
            return appendDefaultHeaders(headers) as Headers;
        }
    }),
    tagTypes: ["bundles", "bundle_ui"],
    endpoints: builder => ({
        getBundlesList: builder.query<BundlesInfo, GetBundlesListRequest>({
            query: ({tenant, project}: GetBundlesListRequest) => {
                return {
                    url: `/api/integrations/bundles?tenant=${tenant}&project=${project}`,
                    method: 'GET',
                    headers: [
                        ["Content-Type", "application/json"]
                    ]
                }
            },
            transformResponse(res: BundlesResponse) {
                return {defaultBundle: res.default_bundle_id, bundles: res.available_bundles}
            },
            providesTags: ["bundles"],
        }),
        getBundleUI: builder.query<string, GetBundleUIRequest>({
            query: ({tenant, project, bundleId}: GetBundleUIRequest) => {
                return {
                    url: `/api/integrations/static/${tenant}/${project}/${bundleId}`,
                    method: 'GET',
                    headers: [
                        ["Content-Type", "text/html"]
                    ],
                    responseHandler: "text"
                }
            },
            providesTags: ["bundle_ui"],
        }),
    })
})

export const {
    useGetBundlesListQuery, useLazyGetBundlesListQuery,
    useGetBundleUIQuery, useLazyGetBundleUIQuery
} = bundlesApiSlice
