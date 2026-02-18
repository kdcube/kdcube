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
import {getCanvasArtifactLink, matchesCanvasArtifact} from "./features/logExtensions/canvas/utils.ts";
import {getWebSearchArtifactLink, matchesWebSearchArtifact} from "./features/logExtensions/webSearch/utils.ts";
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

//chat log extensions
addChatLogExtension(CanvasArtifactType, CanvasLogItem)
addChatLogExtension(CodeExecArtifactType, CodeExecLogItem)
addChatLogExtension(WebSearchArtifactType, WebSearchLogItem)
addChatLogExtension(TimelineTextArtifactType, TimelineTextLogItem)

//canvas extension
addCanvasItemExtension(CanvasArtifactType, CanvasItem, getCanvasArtifactLink, matchesCanvasArtifact)
addCanvasItemExtension(WebSearchArtifactType, WebSearchCanvasItem, getWebSearchArtifactLink, matchesWebSearchArtifact)

//artifact stream parsers (for conversation loader)
addArtifactStreamParsers(
    new IgnoredArtifactStreamReducer(),
    new CanvasArtifactStreamReducer(),
    new CodeExecArtifactStreamReducer(),
    new WebSearchArtifactStreamReducer()
)

createRoot(document.getElementById('root')!).render(
    <StrictMode>
        <Provider store={store}>
            <App/>
        </Provider>
    </StrictMode>,
)
