import {useMemo, useRef, useState} from "react";
import AnimatedExpander from "../../../components/AnimatedExpander.tsx";
import {ChatLogComponentProps} from "../../extensions/logExtesnions.ts";
import {ServiceErrorArtifact, ServiceErrorArtifactType} from "./types.ts";

const ServiceErrorMessage = ({item}: ChatLogComponentProps) => {
    if (item.artifactType !== ServiceErrorArtifactType) {
        throw new Error("not a TimelineTextArtifact")
    }

    const message = useMemo(()=>{
        return (item as ServiceErrorArtifact).content.message
    }, [item]);


    const [errorExpanded, setErrorExpanded] = useState<boolean>(false)
    const errorDetailsRef = useRef<HTMLDivElement>(null)

    return useMemo(() => {
        return <div className={"border border-orange-200 rounded-md px-3 py-2 min-h-0"}>
            <div className={"flex flex-row"}>
                <p className={"text-red-800"}>An error has occurred. Please try again later</p>
                {message && <button
                    className={"ml-auto text-xs cursor-pointer"}
                    onClick={() => setErrorExpanded(prevState => !prevState)}
                >{errorExpanded ? "Hide" : "Show details"}</button>}
            </div>
            {message && <>
                <AnimatedExpander contentRef={errorDetailsRef} expanded={errorExpanded} direction={"vertical"}>
                    <div ref={errorDetailsRef} className={"w-fit"}><span>Error: {message}</span></div>
                </AnimatedExpander>
            </>}
        </div>
    }, [errorExpanded, message])
}

export default ServiceErrorMessage;