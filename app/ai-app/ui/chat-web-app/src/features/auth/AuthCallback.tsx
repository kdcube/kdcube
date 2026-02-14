import {Loader2} from "lucide-react";
import {useEffect} from "react";
import {useAppDispatch, useAppSelector} from "../../app/store.ts";
import {logInCallback} from "./authMiddleware.ts";
import {selectAuthIsLoading, selectNavigateTo} from "./authSlice.ts";
import {useNavigate} from "react-router-dom";

const AuthCallback = ()=>{
    const dispatch = useAppDispatch();
    const navigate = useNavigate();

    const isLoading = useAppSelector(selectAuthIsLoading);
    const navigateTo = useAppSelector(selectNavigateTo);

    useEffect(() => {
        if (!isLoading) {
            dispatch(logInCallback());
        }
    }, [dispatch, isLoading]);

    useEffect(() => {
        if (navigateTo) {
            navigate(navigateTo, {
                replace: true,
            })
        }
    }, [navigateTo, navigate]);

    return <div className="absolute inset-0 bg-white flex items-center justify-center z-50">
        <Loader2 className="h-8 w-8 animate-spin text-gray-600" />
    </div>
}

export default AuthCallback;