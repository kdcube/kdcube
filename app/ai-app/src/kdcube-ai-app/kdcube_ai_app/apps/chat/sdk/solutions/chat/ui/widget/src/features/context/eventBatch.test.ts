import { buildExternalEventBatch } from './eventBatch'
import type { AttachedContext } from './eventBatch'
import { recognizeContextMessageWithTypes, recognizeContextPayload } from './contextMessages'

function assertDeepEqual(actual: unknown, expected: unknown, label: string): void {
  const actualJson = JSON.stringify(actual, null, 2)
  const expectedJson = JSON.stringify(expected, null, 2)
  if (actualJson !== expectedJson) {
    throw new Error(`${label}\nactual:\n${actualJson}\nexpected:\n${expectedJson}`)
  }
}

function idFactory(): (prefix: string) => string {
  let index = 0
  return (prefix: string) => `${prefix}_${++index}`
}

const testContextMessageTypes = {
  attach: 'sample-service-context-attach',
  focus: 'sample-service-context-focus',
  remove: 'sample-service-context-remove',
}

const sampleEventDefaults = {
  userEventSourceId: 'sample_service.main.chat.user',
  attachmentEventSourceId: 'sample_service.main.chat.attachment',
  contextEventSourceId: 'sample_service.context.focus',
  chatSurface: 'sample_service_chat',
  canvasStateEventSourceId: 'sample_service.canvas.state',
  canvasFocusEventSourceId: 'sample_service.canvas.focus',
  canvasSurface: 'sample_service_canvas',
  snapshotEventSourceId: 'sample_service.snapshot',
  snapshotSurface: 'sample_service_wizard',
}

const directMemoryDragPayload = {
  contexts: [
    {
      id: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      kind: 'memory',
      label: 'Family facts about Elena and Timur',
      summary: 'Family facts about Elena and her son Timur',
      ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      logical_path: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      mime: 'application/json',
      event_source_id: 'memory.context',
      surface: 'memory.widget',
      data: { memory_id: 'mem_803986c10e324a16b05a3ba109237c7c' },
    },
  ],
}

const directMemoryDragContexts = recognizeContextPayload(directMemoryDragPayload)

assertDeepEqual(
  directMemoryDragContexts,
  [
    {
      id: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      kind: 'memory',
      label: 'Family facts about Elena and Timur',
      summary: 'Family facts about Elena and her son Timur',
      ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      logicalPath: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      mime: 'application/json',
      eventSourceId: 'memory.context',
      surface: 'memory.widget',
      data: { memory_id: 'mem_803986c10e324a16b05a3ba109237c7c' },
    },
  ],
  'generic context payload unwraps to the underlying mem: ref',
)

assertDeepEqual(
  recognizeContextMessageWithTypes({
    type: 'kdcube.context.attach',
    source: 'memories-widget',
    contexts: directMemoryDragContexts,
  }, testContextMessageTypes),
  directMemoryDragContexts,
  'generic context attach messages preserve normalized memory refs',
)

const directMemoryBatch = buildExternalEventBatch(directMemoryDragContexts, {
  agentId: 'main',
  eventId: idFactory(),
  defaults: sampleEventDefaults,
  text: 'What is this memory?',
})

assertDeepEqual(
  directMemoryBatch.slice(0, 1),
  [
    {
      event_id: 'evt_1',
      reactive: false,
      agent_id: 'main',
      type: 'event.external',
      event_source_id: 'memory.context',
      surface: 'memory.widget',
      hosted_uri: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      payload: {
        mime: 'application/json',
        event_ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
        event: {
          context_role: 'context',
          id: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
          kind: 'memory',
          label: 'Family facts about Elena and Timur',
          summary: 'Family facts about Elena and her son Timur',
          object_ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
          ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
          data: { memory_id: 'mem_803986c10e324a16b05a3ba109237c7c' },
        },
      },
    },
  ],
  'memory context preserves the producer event source id',
)

const landingIntentBatch = buildExternalEventBatch([
  {
    id: 'intent:landing/why-kdcube',
    kind: 'intent',
    label: 'Why KDCube',
    summary: 'Explain why KDCube exists.',
    ref: 'intent:landing/why-kdcube',
    logicalPath: 'intent:landing/why-kdcube',
    mime: 'application/vnd.kdcube.intent+json;version=1',
    eventSourceId: 'intent.context',
    surface: 'website.landing',
    data: {
      object_ref: 'intent:landing/why-kdcube',
      label: 'Why',
      sample: 'Explain why KDCube exists, what problem it solves, and when it is the right choice.',
      preferred_refs: ['ks:wiki/product/landing/why-kdcube.md'],
      fallback_search: 'why KDCube exists product rationale AI applications bundles ReAct agents widgets tools',
    },
  },
], {
  agentId: 'main',
  eventId: idFactory(),
  defaults: sampleEventDefaults,
})

assertDeepEqual(
  landingIntentBatch.slice(0, 1),
  [
    {
      event_id: 'evt_1',
      reactive: false,
      agent_id: 'main',
      type: 'event.external',
      event_source_id: 'intent.context',
      surface: 'website.landing',
      hosted_uri: 'intent:landing/why-kdcube',
      payload: {
        mime: 'application/vnd.kdcube.intent+json;version=1',
        event_ref: 'intent:landing/why-kdcube',
        event: {
          context_role: 'context',
          id: 'intent:landing/why-kdcube',
          kind: 'intent',
          label: 'Why',
          summary: 'Explain why KDCube exists.',
          object_ref: 'intent:landing/why-kdcube',
          ref: 'intent:landing/why-kdcube',
          data: {
            object_ref: 'intent:landing/why-kdcube',
            label: 'Why',
            sample: 'Explain why KDCube exists, what problem it solves, and when it is the right choice.',
            preferred_refs: ['ks:wiki/product/landing/why-kdcube.md'],
            fallback_search: 'why KDCube exists product rationale AI applications bundles ReAct agents widgets tools',
          },
        },
      },
    },
  ],
  'landing intent keeps the chip label separate from the self-contained ReAct payload',
)

const contexts: AttachedContext[] = [
  {
    id: 'canvas:evidence',
    kind: 'canvas',
    label: 'Canvas: evidence',
    summary: 'Evidence board for upload failure triage.',
    ref: 'ext:sample-service/users/user-1/canvases/evidence/latest.json',
    mime: 'application/vnd.kdcube.canvas+json;version=1',
    canvasId: 'canvas:user-1:evidence',
    canvasName: 'evidence',
    revision: 7,
    data: {
      selected_card_ids: ['A1'],
      projection: {
        schema: 'kdcube.canvas.projection.v1',
        canvas_id: 'canvas:user-1:evidence',
        canvas_name: 'evidence',
        canvas_uri: 'canvas:evidence@7',
        revision: 7,
        legend: [
          {
            card_id: 'A1',
            kind: 'user.attachment',
            title: 'upload-error-dialog.png',
            logical_path: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
            mime: 'image/png',
            content_preview: 'Screenshot showing the disappearing upload row.',
            selected: true,
          },
        ],
      },
    },
  },
  {
    id: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
    kind: 'user.attachment',
    label: 'upload-error-dialog.png',
    summary: 'Screenshot showing the disappearing upload row.',
    ref: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
    mime: 'image/png',
    canvasId: 'canvas:user-1:evidence',
    canvasName: 'evidence',
    revision: 7,
    cardId: 'A1',
    cardType: 'user.attachment',
    selected: true,
    data: { story_id: 'story:REC-123' },
  },
  {
    id: 'mem:records/similar-upload-lifecycle',
    kind: 'memory',
    label: 'Similar upload lifecycle record',
    summary: 'Prior memory about attachment lifecycle failures.',
    ref: 'mem:records/similar-upload-lifecycle',
    mime: 'text/markdown',
    canvasId: 'canvas:user-1:evidence',
    canvasName: 'evidence',
    revision: 7,
    cardId: 'M1',
    cardType: 'memory',
    data: { story_id: 'story:REC-123' },
  },
  {
    id: 'snapshot:REC-123',
    kind: 'snapshot',
    label: 'Record snapshot: REC-123',
    summary: 'Upload fails after selecting screenshot.',
    ref: 'ext:sample-service/records/REC-123/snapshots/latest.json',
    mime: 'application/json',
    revision: 12,
    data: {
      schema: 'sample-service.story.context.v1',
      context_role: 'record_story',
      snapshot_kind: 'record_story',
      story_id: 'story:REC-123',
      fields: {
        title: 'Upload fails after selecting screenshot',
        status: 'todo',
        assignee: 'Dana',
      },
      story_definition: {
        kind: 'record_story',
        definition_ref: 'resource:sample-service/story-definitions/record-story.md',
        schema_ref: 'resource:sample-service/story-definitions/record-story-schema.json',
      },
    },
  },
]

const batch = buildExternalEventBatch(contexts, {
  agentId: 'main',
  eventId: idFactory(),
  text: 'Please review the selected evidence and suggest next steps.',
  defaults: sampleEventDefaults,
  target: {
    agent_id: 'main',
    surface: 'sample_service_chat',
    story_id: 'story:REC-123',
    event_source_id: 'sample_service.main.chat.user',
  },
})

assertDeepEqual(
  batch,
  [
    {
      event_id: 'evt_1',
      reactive: false,
      agent_id: 'main',
      type: 'event.canvas',
      event_source_id: 'sample_service.canvas.state',
      surface: 'sample_service_canvas',
      hosted_uri: 'ext:sample-service/users/user-1/canvases/evidence/latest.json',
      payload: {
        mime: 'application/vnd.kdcube.canvas+json;version=1',
        event_ref: 'ext:sample-service/users/user-1/canvases/evidence/latest.json',
        event: {
          context_role: 'canvas',
          id: 'canvas:evidence',
          label: 'Canvas: evidence',
          summary: 'Evidence board for upload failure triage.',
          canvas_id: 'canvas:user-1:evidence',
          canvas_name: 'evidence',
          revision: 7,
          ref: 'ext:sample-service/users/user-1/canvases/evidence/latest.json',
          projection: {
            schema: 'kdcube.canvas.projection.v1',
            canvas_id: 'canvas:user-1:evidence',
            canvas_name: 'evidence',
            canvas_uri: 'canvas:evidence@7',
            revision: 7,
            legend: [
              {
                card_id: 'A1',
                kind: 'user.attachment',
                title: 'upload-error-dialog.png',
                logical_path: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
                mime: 'image/png',
                content_preview: 'Screenshot showing the disappearing upload row.',
                selected: true,
              },
            ],
          },
        },
      },
    },
    {
      event_id: 'evt_2',
      reactive: false,
      agent_id: 'main',
      type: 'event.canvas.focus',
      event_source_id: 'sample_service.canvas.focus',
      surface: 'sample_service_canvas',
      hosted_uri: 'ext:sample-service/users/user-1/canvases/evidence/latest.json',
      payload: {
        mime: 'application/vnd.kdcube.canvas.focus+json;version=1',
        event_ref: 'ext:sample-service/users/user-1/canvases/evidence/latest.json',
        event: {
          context_role: 'canvas_focus',
          canvas_id: 'canvas:user-1:evidence',
          canvas_name: 'evidence',
          canvas_uri: 'canvas:evidence@7',
          revision: 7,
          selection: {
            mode: 'cards',
            reason: 'canvas_selection',
          },
          focused_cards: [
            {
              card_id: 'A1',
              id: 'A1',
              kind: 'user.attachment',
              title: 'upload-error-dialog.png',
              logical_path: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
              ref: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
              mime: 'image/png',
              content_preview: 'Screenshot showing the disappearing upload row.',
              selected: true,
            },
          ],
        },
      },
    },
    {
      event_id: 'evt_3',
      reactive: false,
      agent_id: 'main',
      story_id: 'story:REC-123',
      type: 'event.snapshot',
      event_source_id: 'sample_service.snapshot',
      surface: 'sample_service_wizard',
      hosted_uri: 'ext:sample-service/records/REC-123/snapshots/latest.json',
      payload: {
        mime: 'application/json',
        event_ref: 'ext:sample-service/records/REC-123/snapshots/latest.json',
        event: {
          schema: 'sample-service.story.context.v1',
          context_role: 'record_story',
          snapshot_kind: 'record_story',
          story_id: 'story:REC-123',
          fields: {
            title: 'Upload fails after selecting screenshot',
            status: 'todo',
            assignee: 'Dana',
          },
          story_definition: {
            kind: 'record_story',
            definition_ref: 'resource:sample-service/story-definitions/record-story.md',
            schema_ref: 'resource:sample-service/story-definitions/record-story-schema.json',
          },
          id: 'snapshot:REC-123',
          label: 'Record snapshot: REC-123',
          summary: 'Upload fails after selecting screenshot.',
          revision: 12,
          ref: 'ext:sample-service/records/REC-123/snapshots/latest.json',
        },
      },
    },
    {
      event_id: 'evt_4',
      reactive: false,
      agent_id: 'main',
      story_id: 'story:REC-123',
      type: 'event.external',
      event_source_id: 'sample_service.context.focus',
      surface: 'sample_service_chat',
      hosted_uri: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
      payload: {
        mime: 'image/png',
        event_ref: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
        event: {
          context_role: 'context',
          id: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
          kind: 'user.attachment',
          label: 'upload-error-dialog.png',
          summary: 'Screenshot showing the disappearing upload row.',
          object_ref: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
          ref: 'ext:sample-service/users/user-1/attachments/upload-error-dialog.png',
          data: { story_id: 'story:REC-123' },
        },
      },
    },
    {
      event_id: 'evt_5',
      reactive: false,
      agent_id: 'main',
      story_id: 'story:REC-123',
      type: 'event.external',
      event_source_id: 'sample_service.context.focus',
      surface: 'sample_service_chat',
      hosted_uri: 'mem:records/similar-upload-lifecycle',
      payload: {
        mime: 'text/markdown',
        event_ref: 'mem:records/similar-upload-lifecycle',
        event: {
          context_role: 'context',
          id: 'mem:records/similar-upload-lifecycle',
          kind: 'memory',
          label: 'Similar upload lifecycle record',
          summary: 'Prior memory about attachment lifecycle failures.',
          object_ref: 'mem:records/similar-upload-lifecycle',
          ref: 'mem:records/similar-upload-lifecycle',
          data: { story_id: 'story:REC-123' },
        },
      },
    },
    {
      event_id: 'evt_6',
      type: 'event.user.prompt',
      event_source_id: 'sample_service.main.chat.user',
      reactive: true,
      agent_id: 'main',
      story_id: 'story:REC-123',
      surface: 'sample_service_chat',
      payload: {
        mime: 'text/plain',
        event: {
          text: 'Please review the selected evidence and suggest next steps.',
        },
      },
    },
  ],
  'sample-service context batch',
)

const logicalPathLeaks = batch.filter((event) => Object.prototype.hasOwnProperty.call(event, 'logical_path'))
assertDeepEqual(logicalPathLeaks, [], 'pre-ingress context batch must not set event logical_path')

const canvasFocusEvents = batch.filter((event) => event.event_source_id === 'sample_service.canvas.focus')
assertDeepEqual(
  canvasFocusEvents.map((event) => (event.payload as { event: { focused_cards?: unknown[] } }).event.focused_cards?.length),
  [1],
  'canvas context emits canvas focus for selected cards while separately attached pins remain proxied objects',
)

const loneCanvasMemoryCardBatch = buildExternalEventBatch([
  {
    id: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
    kind: 'memory',
    label: 'Family facts about Elena and Timur',
    summary: 'Family facts about Elena and her son Timur',
    ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
    logicalPath: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
    mime: 'application/json',
    canvasId: 'canvas:user-1:main',
    canvasName: 'main',
    revision: 12,
    cardId: 'M_2026-06-08-14-18-00',
    cardType: 'memory',
    data: {
      memory_id: 'mem_803986c10e324a16b05a3ba109237c7c',
      canvas_context: {
        canvas_id: 'canvas:user-1:main',
        canvas_name: 'main',
        revision: 12,
        card_id: 'M_2026-06-08-14-18-00',
      },
    },
  },
], {
  agentId: 'main',
  eventId: idFactory(),
  defaults: sampleEventDefaults,
  text: 'What do you see?',
})

assertDeepEqual(
  loneCanvasMemoryCardBatch.slice(0, 1),
  [
    {
      event_id: 'evt_1',
      reactive: false,
      agent_id: 'main',
      type: 'event.external',
      event_source_id: 'sample_service.context.focus',
      surface: 'sample_service_chat',
      hosted_uri: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
      payload: {
        mime: 'application/json',
        event_ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
        event: {
          context_role: 'context',
          id: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
          kind: 'memory',
          label: 'Family facts about Elena and Timur',
          summary: 'Family facts about Elena and her son Timur',
          object_ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
          ref: 'mem:mem_803986c10e324a16b05a3ba109237c7c',
          data: {
            memory_id: 'mem_803986c10e324a16b05a3ba109237c7c',
            canvas_context: {
              canvas_id: 'canvas:user-1:main',
              canvas_name: 'main',
              revision: 12,
              card_id: 'M_2026-06-08-14-18-00',
            },
          },
        },
      },
    },
  ],
  'lone canvas memory card is rendered as the underlying memory context, not canvas focus',
)

const loneCanvasFocusEvents = loneCanvasMemoryCardBatch.filter((event) => event.event_source_id === 'sample_service.canvas.focus')
assertDeepEqual(loneCanvasFocusEvents, [], 'lone canvas card does not produce canvas focus without canvas context')

const loneCanvasArtifactCardsBatch = buildExternalEventBatch([
  {
    id: 'ext:sample-service/users/user-1/canvases/main/objects/user-attachments/A1/v000001.png',
    kind: 'user.attachment',
    label: 'upload-error-dialog.png',
    summary: 'Screenshot uploaded directly to canvas.',
    ref: 'ext:sample-service/users/user-1/canvases/main/objects/user-attachments/A1/v000001.png',
    logicalPath: 'ext:sample-service/users/user-1/canvases/main/objects/user-attachments/A1/v000001.png',
    mime: 'image/png',
    canvasId: 'canvas:user-1:main',
    canvasName: 'main',
    revision: 12,
    cardId: 'A1',
    cardType: 'user.attachment',
  },
  {
    id: 'fi:conv_abc/turn_2026-06-08-14-18-00.outputs/problem-statement.md',
    kind: 'file',
    label: 'problem-statement.md',
    summary: 'ReAct-generated file pinned on canvas.',
    ref: 'fi:conv_abc/turn_2026-06-08-14-18-00.outputs/problem-statement.md',
    logicalPath: 'fi:conv_abc/turn_2026-06-08-14-18-00.outputs/problem-statement.md',
    mime: 'text/markdown',
    canvasId: 'canvas:user-1:main',
    canvasName: 'main',
    revision: 12,
    cardId: 'F1',
    cardType: 'file',
  },
  {
    id: 'sample:record:record_d4b5a2e84509',
    kind: 'record.ref',
    label: 'Batch validation record',
    summary: 'record pinned on canvas.',
    ref: 'sample:record:record_d4b5a2e84509',
    logicalPath: 'sample:record:record_d4b5a2e84509',
    mime: 'application/json',
    canvasId: 'canvas:user-1:main',
    canvasName: 'main',
    revision: 12,
    cardId: 'T1',
    cardType: 'record.ref',
  },
], {
  agentId: 'main',
  eventId: idFactory(),
  defaults: sampleEventDefaults,
})

assertDeepEqual(
  loneCanvasArtifactCardsBatch.map((event) => {
    const payloadEvent = (event.payload as { event: { id?: unknown; kind?: unknown; ref?: unknown } }).event
    return {
      source: event.event_source_id,
      hosted_uri: event.hosted_uri,
      id: payloadEvent.id,
      kind: payloadEvent.kind,
      ref: payloadEvent.ref,
    }
  }),
  [
    {
      source: 'sample_service.context.focus',
      hosted_uri: 'ext:sample-service/users/user-1/canvases/main/objects/user-attachments/A1/v000001.png',
      id: 'ext:sample-service/users/user-1/canvases/main/objects/user-attachments/A1/v000001.png',
      kind: 'user.attachment',
      ref: 'ext:sample-service/users/user-1/canvases/main/objects/user-attachments/A1/v000001.png',
    },
    {
      source: 'sample_service.context.focus',
      hosted_uri: 'fi:conv_abc/turn_2026-06-08-14-18-00.outputs/problem-statement.md',
      id: 'fi:conv_abc/turn_2026-06-08-14-18-00.outputs/problem-statement.md',
      kind: 'file',
      ref: 'fi:conv_abc/turn_2026-06-08-14-18-00.outputs/problem-statement.md',
    },
    {
      source: 'sample_service.context.focus',
      hosted_uri: 'sample:record:record_d4b5a2e84509',
      id: 'sample:record:record_d4b5a2e84509',
      kind: 'record.ref',
      ref: 'sample:record:record_d4b5a2e84509',
    },
  ],
  'lone canvas cards for any object type render as their underlying object refs',
)

assertDeepEqual(
  loneCanvasArtifactCardsBatch.filter((event) => event.event_source_id === 'sample_service.canvas.focus'),
  [],
  'lone canvas artifact cards do not produce canvas focus without canvas context',
)

const fileOnlyBatch = buildExternalEventBatch([], {
  agentId: 'main',
  eventId: idFactory(),
  defaults: sampleEventDefaults,
  files: [
    { name: 'crash-log.txt', size: 1204, type: 'text/plain' },
    { name: 'screen.png', size: 64000, type: 'image/png' },
  ],
  target: {
    agent_id: 'main',
    surface: 'sample_service_chat',
    story_id: 'story:REC-123',
  },
})

assertDeepEqual(
  fileOnlyBatch,
  [
    {
      event_id: 'evt_1',
      type: 'event.user.attachment.file',
      event_source_id: 'sample_service.main.chat.attachment',
      reactive: true,
      agent_id: 'main',
      story_id: 'story:REC-123',
      surface: 'sample_service_chat',
      payload: {
        mime: 'text/plain',
        event: {
          filename: 'crash-log.txt',
          size: 1204,
          mime: 'text/plain',
          file_index: 0,
        },
      },
    },
    {
      event_id: 'evt_2',
      type: 'event.user.attachment.file',
      event_source_id: 'sample_service.main.chat.attachment',
      reactive: true,
      agent_id: 'main',
      story_id: 'story:REC-123',
      surface: 'sample_service_chat',
      payload: {
        mime: 'image/png',
        event: {
          filename: 'screen.png',
          size: 64000,
          mime: 'image/png',
          file_index: 1,
        },
      },
    },
  ],
  'file-only sample-service batch uses reactive attachment events',
)

const targetWithoutAgentBatch = buildExternalEventBatch(contexts.slice(0, 1), {
  agentId: 'main',
  eventId: idFactory(),
  defaults: sampleEventDefaults,
  text: 'Use this board.',
  files: [{ name: 'note.md', size: 44, type: 'text/markdown' }],
  target: {
    surface: 'sample_service_chat',
    story_id: 'story:REC-123',
  },
})

assertDeepEqual(
  targetWithoutAgentBatch.map((event) => event.agent_id),
  ['main', 'main', 'main', 'main'],
  'all context/prompt/attachment events inherit the batch agent id when target omits it',
)

const customProfileBatch = buildExternalEventBatch(contexts.slice(0, 1), {
  agentId: 'main',
  eventId: idFactory(),
  text: 'Use this board.',
  files: [{ name: 'note.md', size: 44, type: 'text/markdown' }],
  target: {
    surface: 'versatile_chat',
    story_id: 'story:demo',
  },
  defaults: {
    userEventSourceId: 'versatile.main.chat.user',
    attachmentEventSourceId: 'versatile.main.chat.attachment',
    contextEventSourceId: 'versatile.context.focus',
    chatSurface: 'versatile_chat',
    canvasStateEventSourceId: 'canvas.state',
    canvasFocusEventSourceId: 'canvas.focus',
    canvasSurface: 'canvas',
  },
})

assertDeepEqual(
  customProfileBatch.map((event) => [event.type, event.event_source_id, event.surface]),
  [
    ['event.canvas', 'canvas.state', 'canvas'],
    ['event.canvas.focus', 'canvas.focus', 'canvas'],
    ['event.user.prompt', 'versatile.main.chat.user', 'versatile_chat'],
    ['event.user.attachment.file', 'versatile.main.chat.attachment', 'versatile_chat'],
  ],
  'custom chat event-source profile overrides sample-service defaults',
)
