import {createApi, fetchBaseQuery} from "@reduxjs/toolkit/query/react";
import {RootState} from "../../app/store.ts";
import {selectAuthToken} from "../auth/authSlice.ts";

class Question {
    readonly id: string | number;
    readonly question: string;

    constructor(id: string | number, question: string) {
        this.id = id;
        this.question = question;
    }
}

class QuestionCategory {
    readonly id: string | number;
    readonly category: string;
    readonly items: QuestionsPanelItem[];

    constructor(id: string | number, category: string, items: QuestionsPanelItem[]) {
        this.id = id;
        this.category = category;
        this.items = items;
    }
}

export type QuestionsPanelItem = Question | QuestionCategory;

export const suggestedQuestionsApiSlice = createApi({
    reducerPath: 'suggestedQuestions',
    baseQuery: fetchBaseQuery({
        prepareHeaders(headers, { getState }) {
            const token = selectAuthToken(getState() as RootState)
            if (token) {
                headers.set('authorization', `Bearer ${token}`)
            }
            return headers
        }
    }),
    tagTypes: ['suggestedQuestions'],
    endpoints: builder => ({
        getSuggestedQuestions: builder.query<unknown, {
            tenant: string,
            project: string,
        }>({
            query: ({tenant, project}) => {
                return {
                    url: `/integrations/bundles/${tenant}/${project}/operations/suggestions`,
                    method: 'POST',
                    headers: [
                        ["Content-Type", "application/json"]
                    ],
                    body: "{}" //todo: why?
                }
            },
            // transformResponse(res) {
            //     const parseQuestions = (items: unknown, parentID?: string | number): Question[] | QuestionCategory[] => {
            //         if (items instanceof Array) {
            //             return items.map((item, i) => {
            //                 return new Question(parentID ? `${parentID}_q_${i}` : `q_${i}`, item as string);
            //             })
            //         } else if (items instanceof Object) {
            //             return Object.keys(items).map((category, i) => {
            //                 const itemID = parentID ? `${parentID}_c_${i}` : `c_${i}`
            //                 return new QuestionCategory(itemID, category, parseQuestions((items as Record<string, unknown>)[category], itemID))
            //             })
            //         }
            //         console.warn("unknown question items", items);
            //         return []
            //     }
            //     return  parseQuestions(res);
            // },
            providesTags: ['suggestedQuestions'],
        })
    })
})

export const {useGetSuggestedQuestionsQuery} = suggestedQuestionsApiSlice