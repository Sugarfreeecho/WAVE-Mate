import settingsSource from './modules/settings.js?raw';
import sharedStateAndDialogsSource from './modules/shared-state-and-dialogs.js?raw';
import sessionStoreSource from './state/session-store.js?raw';
import sessionSelectorsSource from './state/session-selectors.js?raw';
import sessionActionsSource from './state/session-actions.js?raw';
import sessionRenderersSource from './state/session-renderers.js?raw';
import messageStoreSource from './state/message-store.js?raw';
import subagentStoreSource from './state/subagent-store.js?raw';
import contextStoreSource from './state/context-store.js?raw';
import sessionEventReducerSource from './state/session-event-reducer.js?raw';
import sessionScrollHistorySource from './modules/session-scroll-history.js?raw';
import tocTodoSource from './modules/toc-todo.js?raw';
import messageRenderingSource from './modules/message-rendering.js?raw';
import subagentSource from './modules/subagent.js?raw';
import eventDispatchSource from './modules/event-dispatch.js?raw';
import sessionManagementSource from './modules/session-management.js?raw';
import sseHandlingSource from './modules/sse-handling.js?raw';
import layoutPanelsSource from './modules/layout-panels.js?raw';

const uiSources = [
    settingsSource,
    sharedStateAndDialogsSource,
    sessionStoreSource,
    sessionSelectorsSource,
    sessionActionsSource,
    sessionRenderersSource,
    messageStoreSource,
    subagentStoreSource,
    contextStoreSource,
    sessionEventReducerSource,
    sessionScrollHistorySource,
    tocTodoSource,
    messageRenderingSource,
    subagentSource,
    eventDispatchSource,
    sessionManagementSource,
    sseHandlingSource,
    layoutPanelsSource,
];

Function('"use strict";\n' + uiSources.join('\n\n') + '\n//# sourceURL=myagent-ui.js')();

if (typeof initUiHoverTips === 'function') {
    initUiHoverTips(document);
}
