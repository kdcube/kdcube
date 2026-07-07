// Scene surface-command contract for this widget (`connections.hub.open`).
//
// A scene host that declares the contract forwards `kdcube.surface.command`
// postMessages whose `target_surface` names one of this widget's surfaces.
// The open payload {tab?, provider?, tiers?, account_id?} rides in `ui_event`
// (the envelope scene hosts forward verbatim); the same keys are accepted at
// the message top level for hosts that relay the raw emitter message.
// The widget answers with `kdcube.surface.command.ack` (the usage-card idiom),
// echoing `command_id` when the command carries one.

export const SURFACE_COMMAND_MESSAGE_TYPE = 'kdcube.surface.command';
export const SURFACE_COMMAND_ACK_MESSAGE_TYPE = 'kdcube.surface.command.ack';

// Surfaces this widget answers for; scene contracts route by target_surface.
export const CONNECTIONS_TARGET_SURFACES = ['connection_hub.connections', 'connection_hub.settings'];

export interface ConnectionsHubOpenCommand {
  targetSurface: string;
  commandId: string;
  tab: string;
  provider: string;
  tiers: string[];
  accountId: string;
}

function tiersFromValue(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (typeof value === 'string') {
    return value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

function stringField(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === 'string' ? value.trim() : '';
}

export function parseConnectionsHubOpen(data: unknown): ConnectionsHubOpenCommand | null {
  if (!data || typeof data !== 'object') return null;
  const raw = data as Record<string, unknown>;
  if (raw.type !== SURFACE_COMMAND_MESSAGE_TYPE) return null;
  const target = typeof raw.target_surface === 'string' ? raw.target_surface.trim().toLowerCase() : '';
  if (!CONNECTIONS_TARGET_SURFACES.includes(target)) return null;
  const action = typeof raw.action === 'string' ? raw.action.trim().toLowerCase() : '';
  if (action && action !== 'open') return null;
  const payload = (raw.ui_event && typeof raw.ui_event === 'object' ? raw.ui_event : raw) as Record<string, unknown>;
  return {
    targetSurface: target,
    commandId: typeof raw.command_id === 'string' ? raw.command_id.trim() : '',
    tab: stringField(payload, 'tab'),
    provider: stringField(payload, 'provider'),
    tiers: tiersFromValue(payload.tiers),
    accountId: stringField(payload, 'account_id'),
  };
}

export function ackConnectionsHubOpen(command: ConnectionsHubOpenCommand, reason: string): void {
  try {
    if (!window.parent || window.parent === window) return;
    const ack: Record<string, unknown> = {
      type: SURFACE_COMMAND_ACK_MESSAGE_TYPE,
      target_surface: command.targetSurface,
      action: 'open',
      reason,
      ts: new Date().toISOString(),
    };
    if (command.commandId) {
      ack.command_id = command.commandId;
      ack.ok = true;
    }
    window.parent.postMessage(ack, '*');
  } catch {
    // Host diagnostics are best-effort only.
  }
}
