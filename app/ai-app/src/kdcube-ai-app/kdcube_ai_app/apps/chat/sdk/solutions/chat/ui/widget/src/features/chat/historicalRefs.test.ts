import { durableHistoricalObjectRef } from './historicalRefs'

function assertEqual(actual: unknown, expected: unknown, label: string): void {
  if (actual !== expected) {
    throw new Error(`${label}\nactual: ${String(actual)}\nexpected: ${String(expected)}`)
  }
}

assertEqual(
  durableHistoricalObjectRef(
    'fi:turn_2026-06-09-01-04-50-786.outputs/pdf/document.html',
    'abc-123',
  ),
  'fi:conv_abc-123.turn_2026-06-09-01-04-50-786.outputs/pdf/document.html',
  'historical fi:turn refs are upgraded to durable fi:conv refs when conversation id is known',
)

assertEqual(
  durableHistoricalObjectRef(
    'fi:conv_abc-123.turn_2026-06-09-01-04-50-786.outputs/pdf/document.html',
    'abc-123',
  ),
  'fi:conv_abc-123.turn_2026-06-09-01-04-50-786.outputs/pdf/document.html',
  'already durable fi:conv refs are unchanged',
)

assertEqual(
  durableHistoricalObjectRef('fi:turn_2026-06-09-01-04-50-786.outputs/pdf/document.html', ''),
  'fi:turn_2026-06-09-01-04-50-786.outputs/pdf/document.html',
  'unscoped historical refs are left unchanged when conversation id is unavailable',
)
