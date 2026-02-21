import {appendDefaultCredentialsHeader} from "../../app/api/utils.ts";
import {createSlice} from "@reduxjs/toolkit";
import {createAppAsyncThunk} from "../../app/withTypes.ts";
import {RootState} from "../../app/store.ts";
import {chatAPIBasePath} from "../../BuildConfig.ts";

interface UserProfile {
    sessionId: string | null;
    userType?: "privileged" | string;
    userName?: string | null;
    userId?: string | null;
    roles?: string[] | null;
    permissions?: string[] | null;
}

// export const userProfileApiSlice = createApi({
//     reducerPath: 'userProfileAPI',
//     baseQuery: fetchBaseQuery({
//         baseUrl: getChatBaseAddress(),
//         prepareHeaders(headers, {getState}) {
//             const token = (getState() as RootState).auth.authToken
//             if (token) {
//                 headers.set('authorization', `Bearer ${token}`)
//             }
//             return headers
//         }
//     }),
//     tagTypes: ['profile'],
//     endpoints: builder => ({
//         getUserProfile: builder.query<UserProfile, void>({
//             query: () => {
//                 return {
//                     url: `/profile`,
//                     method: 'GET',
//                     headers: [
//                         ["Content-Type", "application/json"]
//                     ],
//                     credentials: "include",
//                 }
//             },
//             transformResponse(res: Record<string, unknown>) {
//                 return {
//                     sessionId: res.session_id as string,
//                     userType: res.user_type as string,
//                     userName: res.username as string,
//                     userId: res.user_id as string,
//                     roles: res.roles as string[],
//                     permissions: res.permissions as string[],
//                 } as UserProfile
//             },
//             providesTags: ['profile'],
//         })
//     })
// })
//
// export const {useGetUserProfileQuery} = userProfileApiSlice


interface PostsState {
    profile?: UserProfile | null
    status: 'idle' | 'pending' | 'succeeded' | 'failed'
    error: string | null
}

const initialState: PostsState = {
    status: 'idle',
    error: null
}

export const fetchUserProfile = createAppAsyncThunk('userProfile/fetch', async () => {

    const response = await fetch(`${chatAPIBasePath}/profile`, {
        method: "GET",
        headers: appendDefaultCredentialsHeader({"Content-Type":"application/json"}),
    })

    return response.json()
}, {
    condition(_unused, {getState}) {
        return getState().userProfile.status !== 'pending'
    }
})

const userProfileSlice = createSlice({
    name: 'userProfile',
    initialState,
    reducers: {},

    extraReducers: builder => {
        builder
            .addCase(fetchUserProfile.pending, (state) => {
                state.status = 'pending'
            })
            .addCase(fetchUserProfile.fulfilled, (state:PostsState, action) => {
                state.status = 'succeeded'
                state.profile = {
                    sessionId: action.payload.session_id as string,
                    userType: action.payload.user_type as string,
                    userName: action.payload.username as string,
                    userId: action.payload.user_id as string,
                    roles: action.payload.roles as string[],
                    permissions: action.payload.permissions as string[],
                } as UserProfile

            })
            .addCase(fetchUserProfile.rejected, (state, action) => {
                state.status = 'failed'
                state.error = action.error.message ?? 'Unknown Error'
            })
    }
})

export default userProfileSlice.reducer

export const selectUserProfileStatus = (state: RootState) => state.userProfile.status
export const selectUserProfileError = (state: RootState) => state.userProfile.error
export const selectUserProfile = (state: RootState) => state.userProfile.profile
