import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {selectAuthIsLoading, selectIsLoggedIn} from "./authSlice.ts";
import {useEffect} from "react";
import {logIn} from "./authMiddleware.ts";
import {WithReactChildren} from "../../types/common.ts";

type WithAuthRequiredProps = WithReactChildren

const WithAuthRequired = ({children}: WithAuthRequiredProps) => {
    const loggedIn = useAppSelector(selectIsLoggedIn)
    const loading = useAppSelector(selectAuthIsLoading)

    const dispatch = useAppDispatch();
    const destination = typeof window !== "undefined"
        ? `${window.location.pathname}${window.location.search}${window.location.hash}`
        : undefined;

    useEffect(() => {
        if (!loggedIn && !loading) {
            dispatch(logIn(destination))
        }
    }, [loggedIn, loading, dispatch, destination]);

    if (!loggedIn) {
        return <main className="app-startup" role="status" aria-live="polite">
            <div className="app-startup__content">
                <p>{loading ? "Checking your session..." : "Taking you to sign in..."}</p>
                {!loading && (
                    <button type="button" onClick={() => dispatch(logIn(destination))}>
                        Sign in
                    </button>
                )}
            </div>
        </main>;
    }

    return (
        <>{children}</>
    )
}

export default WithAuthRequired;
