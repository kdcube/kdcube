---
id: ks:docs/sdk/agents/react/event-source/events-blocks-and-rendering-README.md
title: "Events, Blocks, And Rendering"
summary: "Matrix of ReAct event-source phases, timeline spots, built-in sources, and current implementation status."
tags: ["sdk", "agents", "react", "event-source", "timeline", "rendering"]
keywords: ["timeline_projection", "block_production", "announce_production", "compaction_projection", "tool_call_validation", "cache markers"]
see_also:
  - ks:docs/sdk/agents/react/event-source/event-source-README.md
  - ks:docs/sdk/agents/react/event-source/block-production-README.md
  - ks:docs/sdk/agents/react/timeline-README.md
  - ks:docs/sdk/agents/react/context-caching-README.md
  - ks:docs/sdk/agents/react/compaction-README.md
---
# Events, Blocks, And ReAct Rendering

This page maps where events become timeline blocks and where those blocks are
projected for model-visible context. The policy layer must respect the existing
ReAct render order:

```text
persistent timeline blocks
    -> TTL pruning / compaction / hidden replacements
    -> render directives
    -> cache markers
    -> non-cached tail: sources pool + ANNOUNCE
    -> model message blocks
```

ANNOUNCE is tail material. It is recomputed for the decision render and appended
after cache markers. It is not stored as a normal timeline block unless a
separate materializer writes a durable artifact/ref.

## Phase Matrix

| # | Spot | Current implementation | `tool_call_validation` | `block_production` | `timeline_projection` | `announce_production` | `compaction_projection` | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | User message block creation | `build_user_input_blocks()` in `layout.py` | Not applicable. | Hardcoded `user.prompt`; not yet declared as a source. | Existing renderer handles visible text. | Current user context can be mentioned separately. | Existing summary selection includes it. | Future first-party user source. |
| 2 | Attachment block creation | `build_user_attachment_blocks()` in `layout.py` | Not applicable. | Hardcoded `user.attachment.meta` plus optional binary/text blocks. | Existing render directives and binary caps apply. | No source policy yet. | Existing compaction keeps refs/metadata through normal logic. | Future attachment source. |
| 3 | External event arrival | `ContextBrowser.apply_external_events()` in `browser.py` | Not applicable. | Arrival checks consume/deflect events. | Not projected at arrival. | Not involved. | Not involved. | Transport-owned, not policy-owned. |
| 4 | External event to timeline blocks | `_blocks_from_external_event()` in `browser.py` | Not applicable. | Feature-flagged stamping exists for followup/steer with `event_source_id` and `event_id`; block shapes remain `user.followup` / `user.steer`. | Stamped blocks can be addressed by policy. | Future source/story policies can add tail material. | Future source/story policies can preserve or replace. | Built-in declarations exist; custom external event builders are pending. |
| 5 | Current-turn deferral | `_fold_external_events_initial()` / `_fold_external_events()` in `browser.py` | Not applicable. | Defers current-turn events before contribution. | Preserves owner turn before projection. | Not involved. | Not involved. | Keep this ownership rule. |
| 6 | Contribution filtering | `_filter_contribution_blocks()` and timeline contribution in `browser.py` | Not applicable. | Filters duplicate/invalid contributed blocks. | Projection sees only contributed blocks. | Not involved. | Compaction sees contributed blocks. | Event metadata must be attached before final contribution. |
| 7 | ReAct round blocks | `ReactRound.start()`, `thinking()`, `note()`, `decision_raw()` in `round.py` | Not applicable. | Harness-owned blocks. | Existing renderer handles them. | Not source-materialized. | Existing compaction preserves compact round metadata. | Keep as harness blocks. |
| 8 | Tool call validation | `handle_external_tool()` after `ref:` binding and before `tool_call_block()` | Policies can mutate `final_params`, emit notices/blocks, update state, or stop/retry. | Not result production. | Not visible projection. | Not announce. | Not compaction. | Implemented for exec and rendering input preparation. |
| 9 | Tool call block | `tool_call_block()` in `tools/common.py` | Validation has already run. | Harness emits generic `react.tool.call`; feature flag can stamp event identity. | Renderer handles `react.tool.call`; policies can target it later. | Not yet used. | Existing compaction still has hardcoded tool-call handling. | Tool-call production stays harness-owned. |
| 10 | Tool result/artifact blocks | `handle_external_tool()` plus artifact helpers | Already completed before execution. | Source-specific policies produce result rows, artifact rows, declared-file rows, source rows, and notice rows before shared builders create final blocks. | New phase can mutate stamped blocks before render. | Future phase can inspect timeline and append tail blocks. | New phase can mutate selected blocks before summary/preservation. | Block production works for external SDK tools under the flag; old path remains fallback. |
| 11 | Sources pool | Tool result handlers and `timeline.sources_pool` | Already after validation. | Search/fetch use `react.block_production.exploration_results`; rows merge into `sources_pool`. | Sources pool is rendered as tail, not as durable timeline history. | Sources can influence announce only through explicit announce policy. | Sources pool is not compacted as timeline history. | Implemented for `web_search` and `web_fetch`. |
| 12 | ANNOUNCE construction | `build_announce_text()` in `layout.py` | Not applicable. | Not durable block production. | Not timeline projection. | Future phase should call source/materializer policies and append ephemeral tail blocks. | Not compaction. | Policy type exists; materialization is pending. |
| 13 | Render probe before compaction | `_render_locked()` in `timeline.py` | Not applicable. | Uses already-produced blocks. | Projection can affect token pressure. | ANNOUNCE tail affects pressure and must be capped. | Probe precedes compaction choice. | Keep projections bounded. |
| 14 | Cache TTL segmentation | `apply_cache_ttl_pruning()` in `session.py` | Not applicable. | Uses persisted blocks. | Feature-flagged phase call exists after temporary `_react_timeline_segment` marks are patched. | Not involved. | Not involved. | Default hardcoded pruning remains. |
| 15 | Compaction trigger and cut | `sanitize_context_blocks()` in `timeline.py` | Not applicable. | Uses persisted blocks. | Render path already happened earlier. | Not involved. | Feature-flagged phase seam exists for mutable compaction blocks. | Preservation logic remains partly hardcoded. |
| 16 | Compaction history selection | `_is_meaningful_compaction_history_block()` and summarizer input | Not applicable. | Uses selected blocks. | Not involved. | Not involved. | Policies should hide, replace, or preserve refs before summarizer input. | Needs source/story-specific defaults. |
| 17 | Compaction preservation | `_build_compacted_external_event_blocks()` / `_build_compacted_round_blocks()` | Not applicable. | Uses produced blocks. | Not involved. | Not involved. | Still hardcodes followup/steer and tool-round preservation. | Replace with event-source policies later. |
| 18 | Slice after compaction summary | `_slice_after_compaction_summary()` in `timeline.py` | Not applicable. | Uses persisted blocks and summary range. | Hidden/replacement state is rendered after slicing. | Not involved. | Must leave discoverable refs for material that should survive summaries. | Keep refs explicit. |
| 19 | Hidden replacements | `_apply_hidden_replacements()` in `timeline.py` | Not applicable. | Uses persisted blocks with hidden flags. | Policies can set `hidden` and `replacement_text`. | Not involved. | Same fields can guide compaction projection. | `react.timeline_projection.hide_by_segment` exists. |
| 20 | Render directives | `_apply_render_directives_before_cache()` and `build_timeline_render_directive()` | Not applicable. | Uses already-shaped blocks. | Consumes policy decisions; does not discover sources. | Not involved. | Not involved. | Final render hygiene layer. |
| 21 | Cache marker assignment | `_apply_cache_markers()` in `timeline.py` | Not applicable. | Uses visible timeline blocks. | Must run only after projection so cache points are stable. | ANNOUNCE is appended after cache markers. | Not involved. | Preserve existing cache strategy. |
| 22 | Tail append | `_append_tail_blocks()` in `timeline.py` | Not applicable. | Not persistent production. | Sources/announce tail appended after projection. | ANNOUNCE blocks are appended here after being produced. | Not involved. | Tail is non-cached and non-durable unless separately materialized. |
| 23 | Block-to-message rendering | `_blocks_to_message_blocks()` in `timeline.py` | Not applicable. | Consumes typed blocks. | Converts already-decided blocks to model-visible messages. | Converts tail blocks too. | Not involved. | No source lookup here. |
| 24 | Turn finalization | `_exit_node()` in `v3/runtime.py` | Not applicable. | Stores `react.turn.finalize`, clears ANNOUNCE, appends state/exit. | Later renders normally. | Clears ephemeral announce state. | Existing compaction can summarize finalization. | Story state persists separately. |
| 25 | Persistence and recovery | Timeline persistence and read/search tools | Not applicable. | Persists timeline blocks and sources pool. | Later read/search sees durable blocks/refs. | ANNOUNCE is not persisted. | Compacted blocks/refs become recovery anchors. | Snapshot refs and cross-conversation paths must remain durable. |

## Built-In Event Source Matrix

| Source/family | Occurrence identity | Durable block shape | `tool_call_validation` | `block_production` | `timeline_projection` | `announce_production` | `compaction_projection` |
|---|---|---|---|---|---|---|---|
| `react.followup` | External event `event_id` | `user.followup` plus hosted attachment blocks | N/A | Stamped during external-event fold; custom builder pending. | Addressable when stamped. | Pending. | Preservation still partly hardcoded. |
| `react.steer` | External event `event_id` | `user.steer` plus hosted attachment blocks | N/A | Stamped during external-event fold; custom builder pending. | Addressable when stamped. | Pending. | Preservation still partly hardcoded. |
| `react.write` | `tool_call_id` | Native `react.tool.call` / `react.tool.result` / `react.note` / `fi:` file blocks | Native handler. | Native handler writes blocks directly. | Identity policy. | Pending. | Identity policy; legacy compaction logic remains. |
| `react.memsearch` | `tool_call_id` | Native source/recovery result blocks | Native handler. | Native handler writes blocks directly. | Identity policy. | Pending. | Identity policy. |
| `web_tools.web_search`, `web_tools.web_fetch` | `tool_call_id` | `react.tool.call`, `react.tool.result`, `sources_pool` rows | None. | `tool_default`, `exploration_results`, `generic_result_item`. | Identity by default. | Pending. | Identity by default. |
| `browser_tools.*` | `tool_call_id` | `react.tool.call`, JSON/text `react.tool.result`, optional declared files | None. | `tool_default`, `generic_result_item`, `declared_file_items`. | Identity by default. | Pending. | Identity by default. |
| `rendering_tools.write_*` | `tool_call_id` | `react.tool.call`, artifact meta/content blocks, hosted refs | `rendering_tools.tool_call_validation.prepare_inputs`. | `write_tool_result`, `declared_file_items`. | Identity by default. | Pending. | Identity by default. |
| `exec_tools.execute_code_python` | `tool_call_id` | `react.tool.code`, `react.tool.call`, report/result blocks, artifact rows | `exec_tools.tool_call_validation.exec_preflight`. | `exec_tools.block_production.exec_result`. | Identity by default. | Pending. | Identity by default. |
| `memory.*` | `tool_call_id` | Structured JSON `react.tool.result` blocks | None. | `generic_result_item`, `declared_file_items`. | Identity by default. | Pending. | Identity by default. |
| Custom structured tools | `tool_call_id` | Structured JSON/text/file result blocks | Optional source policy. | Default structured policy pack when no declaration is found. | Identity unless declared otherwise. | Pending. | Identity unless declared otherwise. |

## Cache And Segment Rules

Timeline/compaction policies operate on already-produced blocks. For cold-cache
or TTL pruning paths, the caller patches temporary segment metadata before
invoking policy handlers:

```text
meta._react_timeline_segment = current | intact_recent | recent | old | compacted
```

That mark is phase-local. The caller must remove it after policy application.
Policy-produced durable changes, such as `hidden` or `replacement_text`, remain.
Cache markers are assigned after timeline projection and before tail material is
appended. Policies must not change this ordering.

