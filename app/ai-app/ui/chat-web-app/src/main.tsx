import {StrictMode} from 'react'
import {createRoot} from 'react-dom/client'
import './index.css'
import App from "./App.tsx";
import {store} from "./app/store.ts";
import {Provider} from "react-redux";
import {addChatLogExtension} from "./features/extensions/logExtesnions.ts";
import {addCanvasItemExtension} from "./features/extensions/canvasExtensions.ts";
import {CanvasLogItem} from "./features/logExtensions/canvas/CanvasLogItem.tsx";
import CodeExecLogItem from "./features/logExtensions/codeExec/CodeExecLogItem.tsx";
import WebSearchLogItem from "./features/logExtensions/webSearch/WebSearchLogItem.tsx";
import TimelineTextLogItem from "./features/logExtensions/timelineText/TimelineTextLogItem.tsx";
import {
    copyCanvasArtifact,
    getCanvasArtifactLink,
    getCanvasArtifactTitle,
    matchesCanvasArtifact,
    saveCanvasArtifact
} from "./features/logExtensions/canvas/utils.ts";
import {
    copyWebSearchArtifact,
    getWebSearchArtifactLink,
    getWebSearchArtifactTitle,
    matchesWebSearchArtifact,
    saveWebSearchArtifact
} from "./features/logExtensions/webSearch/utils.ts";
import {CanvasArtifactType} from "./features/logExtensions/canvas/types.ts";
import {CodeExecArtifactType} from "./features/logExtensions/codeExec/types.ts";
import {WebSearchArtifactType} from "./features/logExtensions/webSearch/types.ts";
import {TimelineTextArtifactType} from "./features/logExtensions/timelineText/types.ts";
import CanvasItem from "./features/logExtensions/canvas/CanvasItem.tsx";
import WebSearchCanvasItem from "./features/logExtensions/webSearch/WebSearchCanvasItem.tsx";
import {addArtifactStreamParsers} from "./features/conversations/conversationsMiddleware.ts";
import {IgnoredArtifactStreamReducer} from "./features/logExtensions/ignored/IgnoredArtifactStreamReducer.ts";
import {CanvasArtifactStreamReducer} from "./features/logExtensions/canvas/CanvasArtifactStreamReducer.ts";
import {CodeExecArtifactStreamReducer} from "./features/logExtensions/codeExec/CodeExecArtifactStreamReducer.ts";
import {WebSearchArtifactStreamReducer} from "./features/logExtensions/webSearch/WebSearchArtifactStreamReducer.ts";
import {WebFetchArtifactStreamReducer} from "./features/logExtensions/webFetch/WebFetchArtifactStreamReducer.ts";
import {WebFetchArtifactType} from "./features/logExtensions/webFetch/types.ts";
import WebFetchLogItem from "./features/logExtensions/webFetch/WebFetchLogItem.tsx";
import ServiceErrorMessage from "./features/logExtensions/service/ServiceErrorMessage.tsx";
import {ServiceErrorArtifactType} from "./features/logExtensions/service/types.ts";

//chat log extensions
addChatLogExtension(CanvasArtifactType, CanvasLogItem)
addChatLogExtension(CodeExecArtifactType, CodeExecLogItem)
addChatLogExtension(WebSearchArtifactType, WebSearchLogItem)
addChatLogExtension(TimelineTextArtifactType, TimelineTextLogItem)
addChatLogExtension(WebFetchArtifactType, WebFetchLogItem)
addChatLogExtension(ServiceErrorArtifactType, ServiceErrorMessage)

//canvas extension
addCanvasItemExtension(CanvasArtifactType, {
    component: CanvasItem,
    linkGenerator: getCanvasArtifactLink,
    artifactLinkComparator: matchesCanvasArtifact,
    artifactTitleGenerator: getCanvasArtifactTitle,
    artifactCopyHandler: copyCanvasArtifact,
    artifactSaveHandler: saveCanvasArtifact,
})

addCanvasItemExtension(WebSearchArtifactType, {
    component: WebSearchCanvasItem,
    linkGenerator: getWebSearchArtifactLink,
    artifactLinkComparator: matchesWebSearchArtifact,
    artifactTitleGenerator: getWebSearchArtifactTitle,
    artifactCopyHandler: copyWebSearchArtifact,
    artifactSaveHandler: saveWebSearchArtifact,
})

//artifact stream parsers (for conversation loader)
addArtifactStreamParsers(
    new IgnoredArtifactStreamReducer({marker: "subsystem", subtypes: ["conversation.turn.status"]}),
    new CanvasArtifactStreamReducer(),
    new CodeExecArtifactStreamReducer(),
    new WebSearchArtifactStreamReducer(),
    new WebFetchArtifactStreamReducer()
)

createRoot(document.getElementById('root')!).render(
    <StrictMode>
        <Provider store={store}>
            <App/>
        </Provider>
    </StrictMode>,
)
