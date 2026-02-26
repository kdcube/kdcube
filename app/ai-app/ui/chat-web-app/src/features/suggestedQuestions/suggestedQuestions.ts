import {createApi, fetchBaseQuery} from "@reduxjs/toolkit/query/react";
import {appendDefaultCredentialsHeader} from "../../app/api/utils.ts";

export interface Question {
    type: "question";
    id: string;
    text: string;
}

export interface QuestionCategory {
    type: "category";
    id: string;
    text: string;
    items: QuestionsPanelItem[];
}

export type QuestionsPanelItem = Question | QuestionCategory;

export const suggestedQuestionsApiSlice = createApi({
    reducerPath: 'suggestedQuestions',
    baseQuery: fetchBaseQuery({
        prepareHeaders(headers) {
            return appendDefaultCredentialsHeader(headers) as Headers;
        }
    }),
    tagTypes: ['suggestedQuestions'],
    endpoints: builder => ({
        getSuggestedQuestions: builder.query<QuestionsPanelItem[], {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}) => {
                return {
                    url: `/api/integrations/bundles/${tenant}/${project}/operations/suggestions`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}" //todo: why?
                }
            },
            transformResponse(res:{suggestions:unknown}) {
                const parseQuestions = (items: unknown, parentID?: string | number): Question[] | QuestionCategory[] => {
                    if (items instanceof Array) {
                        return items.map((item, i) => {
                            const id = parentID ? `${parentID}_q_${i}` : `q_${i}`;
                            return {
                                type: "question",
                                id,
                                text: item as string
                            }
                        })
                    } else if (items instanceof Object) {
                        return Object.keys(items).map((category, i) => {
                            const id = parentID ? `${parentID}_c_${i}` : `c_${i}`
                            return {
                                type: "category",
                                id,
                                text: category,
                                items: parseQuestions((items as Record<string, unknown>)[category], id)
                            }
                        })
                    }
                    console.warn("unknown question items", items);
                    return []
                }
                return parseQuestions(res.suggestions);
            },
            providesTags: ['suggestedQuestions'],
        })
    })
})

export const {useGetSuggestedQuestionsQuery} = suggestedQuestionsApiSlice
