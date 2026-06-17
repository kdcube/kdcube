/**
 * @kdcube/components-core/canvas — framework-agnostic canvas logic.
 *
 * The pure, view-free layer of the canvas: the card/document data model and
 * patch/projection transforms (canvasModel), the wire types (canvasTypes), the
 * ingress builders that turn selected text / chat artifacts / search results into
 * cards (ingress), the postMessage ingress parser (ingressBridge), context-pin
 * normalization (contextTypes), and id helpers (ids).
 *
 * No React, no DOM — a non-React client can drive the same logic. The React view
 * (CanvasBoard) lives in @kdcube/components-react/canvas and imports from here.
 * The stateful engine controller (createCanvasEngine) is added on top of this.
 */
export * from './canvasTypes'
export * from './contextTypes'
export * from './ids'
export * from './ingressBridge'
export * from './canvasModel'
export * from './ingress'
