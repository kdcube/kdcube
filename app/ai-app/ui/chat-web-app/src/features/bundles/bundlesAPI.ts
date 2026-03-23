import {createApi, fetchBaseQuery} from "@reduxjs/toolkit/query/react";
import {appendDefaultCredentialsHeader} from "../../app/api/utils.ts";
import {ChatScope} from "../chat/chatTypes.ts";
import {BundlesInfo, BundlesResponse} from "./types.ts";

export const bundlesApiSlice = createApi({
    reducerPath: 'bundlesAPI',
    baseQuery: fetchBaseQuery({
        prepareHeaders(headers) {
            return appendDefaultCredentialsHeader(headers) as Headers;
        }
    }),
    tagTypes: ["bundles"],
    endpoints: builder => ({
        getBundlesList: builder.query<BundlesInfo, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}: ChatScope) => {
                return {
                    url: `/admin/integrations/bundles?tenant=${tenant}&project=${project}`,
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

    })
})

export const {
    useGetBundlesListQuery, useLazyGetBundlesListQuery,
} = bundlesApiSlice