/**
 * Standalone capabilities view-model for the served capability-picker widget.
 *
 * The full chat widget drives the picker through the chat engine; a served
 * widget has no conversation and no engine — it talks straight to the same
 * two operations (`agent_capabilities` GET-shaped read, `agent_selection_update`
 * merge-write) with the widget's own auth. This hook reproduces the engine's
 * capabilities contract (optimistic toggle + debounced merge-write + explicit
 * decisions) over injected fetchers and shapes the result as the `vm` slice
 * `useCapabilityPickerBody` consumes — the picker logic itself is not forked.
 */

import { useMemo, useRef, useState } from 'react'
import {
  applySelectionPatch,
  mergeSelectionPatches,
} from '@kdcube/components-core/chat'
import type {
  AgentCachePolicy,
  AgentCapabilitiesInventory,
  AgentModelPick,
  AgentSelectionDisabled,
  AgentSelectionPatch,
  AgentSelectionPending,
  ConnectionsConsentOpen,
} from '@kdcube/components-core/chat'
import type { ChatViewModel } from '../../viewModel.ts'

export interface StandaloneCapabilitiesResponse {
  agent?: string
  capabilities?: AgentCapabilitiesInventory | null
  selection?: {
    disabled?: AgentSelectionDisabled
    model?: AgentModelPick | null
    pending?: AgentSelectionPending | null
  } | null
  cache_policy?: AgentCachePolicy | null
}

export interface StandaloneSelectionWriteOptions {
  apply?: 'now' | 'next_conversation' | 'when_cold'
  cachePolicy?: Record<string, string>
}

export interface StandaloneCapabilityRuntime {
  /** The bundle agent whose inventory this page manages. */
  agentId: string
  fetchCapabilities(): Promise<StandaloneCapabilitiesResponse>
  submitUpdate(
    patch: AgentSelectionPatch,
    options?: StandaloneSelectionWriteOptions,
  ): Promise<StandaloneCapabilitiesResponse>
  /** Opens the Connection Hub consent plan (deep link). Absent = consent
   *  chips render as read-only state tags. */
  openConnections?: (consent: ConnectionsConsentOpen) => void
}

const SAVE_DEBOUNCE_MS = 600

/** A `vm`-shaped object for `useCapabilityPickerBody` / `CapabilityPickerPage`
 *  backed by plain operation calls. Only the slice the picker reads is real;
 *  the cast is the documented seam (the picker touches nothing else). */
export function useStandaloneCapabilitiesVm(runtime: StandaloneCapabilityRuntime): ChatViewModel {
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [agent, setAgent] = useState<string>(runtime.agentId)
  const [inventory, setInventory] = useState<AgentCapabilitiesInventory | null>(null)
  const [disabled, setDisabled] = useState<AgentSelectionDisabled>({})
  const [model, setModel] = useState<AgentModelPick | null>(null)
  const [cachePolicy, setCachePolicy] = useState<AgentCachePolicy | null>(null)
  const [pending, setPending] = useState<AgentSelectionPending | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const statusRef = useRef(status)
  statusRef.current = status
  const timerRef = useRef<number | null>(null)
  const pendingPatchRef = useRef<AgentSelectionPatch | null>(null)

  const applyResponseSelection = (response: StandaloneCapabilitiesResponse) => {
    setDisabled(response.selection?.disabled ?? {})
    setModel(response.selection?.model ?? null)
    setPending(response.selection?.pending ?? null)
  }

  const load = async (opts?: { force?: boolean }) => {
    if (statusRef.current === 'loading') return
    if (statusRef.current === 'ready' && !opts?.force) return
    setStatus('loading')
    setError(null)
    try {
      const response = await runtime.fetchCapabilities()
      setAgent(response.agent || runtime.agentId)
      setInventory(response.capabilities ?? null)
      setCachePolicy(response.cache_policy ?? null)
      applyResponseSelection(response)
      setStatus('ready')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setStatus('error')
    }
  }

  const flush = async () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    const patch = pendingPatchRef.current
    pendingPatchRef.current = null
    if (!patch) return
    setSaving(true)
    try {
      const response = await runtime.submitUpdate(patch)
      applyResponseSelection(response)
      setSaveError(null)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const toggle = (patch: AgentSelectionPatch) => {
    setDisabled((current) => applySelectionPatch(current, patch))
    if (patch.model !== undefined) setModel(patch.model ?? null)
    pendingPatchRef.current = mergeSelectionPatches(pendingPatchRef.current ?? {}, patch)
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      void flush()
    }, SAVE_DEBOUNCE_MS)
  }

  const decide = async (
    patch: AgentSelectionPatch,
    options: StandaloneSelectionWriteOptions = {},
  ) => {
    const apply = options.apply ?? 'now'
    if (apply === 'now') {
      setDisabled((current) => applySelectionPatch(current, patch))
      if (patch.model !== undefined) setModel(patch.model ?? null)
    }
    setSaving(true)
    try {
      const response = await runtime.submitUpdate(patch, options)
      applyResponseSelection(response)
      setSaveError(null)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return useMemo(() => {
    const vm = {
      authed: true,
      agentId: agent,
      state: {
        // A served page has no conversation: nothing is cached, so toggles
        // apply directly (the confirm flow is a warm-conversation concern).
        turns: [] as unknown[],
        toolSpotlight: null,
      },
      capabilities: {
        status,
        error,
        agent,
        inventory,
        disabled,
        model,
        cachePolicy,
        pending,
        saving,
        saveError,
        load,
        toggle,
        decide,
      },
      connections: {
        available: () => Boolean(runtime.openConnections),
        open: (_source: string, consent?: ConnectionsConsentOpen) => {
          if (consent) runtime.openConnections?.(consent)
        },
      },
    }
    return vm as unknown as ChatViewModel
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, error, agent, inventory, disabled, model, cachePolicy, pending, saving, saveError])
}
