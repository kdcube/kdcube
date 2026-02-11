# Plan tracking (react v2)

This system treats plans as **explicit snapshots** persisted to the timeline and surfaced in the announce section when active.  
Plans are **not inferred**. They are created by the coordinator and updated when the decision acknowledges progress.
All plan history lives **only in timeline blocks** (no separate in‑memory plan history).

## 1) Plan lifecycle

### Create
When the coordinator emits a plan, runtime creates a `PlanSnapshot`:
- assigns a `plan_id` (if missing)
- records `origin_turn_id`
- records `created_ts`
- captures `steps[]` and empty `status`

It is persisted as a **react.plan** JSON block.

### Update
When decision acknowledges progress in `notes`, runtime:
- parses ✓ / ✗ / □ marks
- updates status
- writes a new **react.plan** JSON block (same plan_id) with updated status
- writes a **react.plan.ack** text block for readability

### Active vs inactive
The announce section shows **only the latest active plan**:
- not completed
- older plans remain in timeline history but are not re‑announced

## 2) Plan mode (coordinator)

The coordinator selects how to handle the plan each turn:

```
plan: {
  mode: "active" | "new" | "update" | "close",
  steps: [...],   // only for new|update
  plan_id: "optional-id"
}
```

Rules:
- `active` → keep the latest plan active; do NOT emit steps
- `new` → emit fresh steps
- `update` → emit updated steps and mention in instructions_for_downstream
- `close` → discard the active plan; do NOT emit steps

## 3) Timeline representation

### Plan snapshot block (JSON)
```
type: "react.plan"
mime: "application/json"
path: "ar:<turn_id>.react.plan.<plan_id>"
text: { ...PlanSnapshot... }
```

### Ack block (text)
```
type: "react.plan.ack"
mime: "text/markdown"
path: "ar:<turn_id>.react.plan.ack.<iteration>"
text:
  ✓ 1. ...
  … 2. ...
```

## 4) Announce behavior

When `timeline.render(include_announce=True)`:
- Active plans are synthesized into **react.plan.active** blocks.
- The active block shows:
  - plan id
  - origin turn id
  - last acknowledgement timestamp
  - steps with current marks

## 5) Turn age rule

No age filtering: announce always shows the latest active plan (if any).
