/**
 * Standalone capabilities view-model for the served capability-picker widget.
 *
 * The full chat widget drives the picker through the chat engine; a served
 * widget has no engine and talks straight to the same two operations
 * (`agent_capabilities` read, `agent_selection_update` merge-write) with its
 * own auth. Its injected fetchers decide whether those calls carry a
 * conversation id or target the unscoped baseline. This hook reproduces the engine's
 * capabilities contract (local draft + explicit save + explicit cache
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
  /** Opens the Connection Hub: with a consent payload it lands on the
   *  provider-connections card; without one (the bare "Manage connections"
   *  row) it opens the hub itself. Absent = consent chips render as
   *  read-only state tags and the row hides. */
  openConnections?: (consent?: ConnectionsConsentOpen) => void
}

/** A `vm`-shaped object for `useCapabilityPickerBody` / `CapabilityPickerPage`
 *  backed by plain operation calls. Only the slice the picker reads is real;
 *  the cast is the documented seam (the picker touches nothing else). */
export function useStandaloneCapabilitiesVm(
  runtime: StandaloneCapabilityRuntime,
  options: { spotlight?: { tools: string[]; nonce: number } | null } = {},
): ChatViewModel {
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [agent, setAgent] = useState<string>(runtime.agentId)
  const [inventory, setInventory] = useState<AgentCapabilitiesInventory | null>(null)
  const [disabled, setDisabled] = useState<AgentSelectionDisabled>({})
  const [model, setModel] = useState<AgentModelPick | null>(null)
  const [cachePolicy, setCachePolicy] = useState<AgentCachePolicy | null>(null)
  const [pending, setPending] = useState<AgentSelectionPending | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const statusRef = useRef(status)
  statusRef.current = status
  const pendingPatchRef = useRef<AgentSelectionPatch | null>(null)

  const applyResponseSelection = (
    response: StandaloneCapabilitiesResponse,
    queuedPatch: AgentSelectionPatch | null = null,
  ) => {
    const responseDisabled = response.selection?.disabled ?? {}
    const responseModel = response.selection?.model ?? null
    setDisabled(queuedPatch ? applySelectionPatch(responseDisabled, queuedPatch) : responseDisabled)
    setModel(queuedPatch?.model !== undefined ? queuedPatch.model ?? null : responseModel)
    setPending(response.selection?.pending ?? null)
    setDirty(Boolean(queuedPatch))
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
    const patch = pendingPatchRef.current
    pendingPatchRef.current = null
    if (!patch) return
    setSaving(true)
    try {
      const response = await runtime.submitUpdate(patch)
      applyResponseSelection(response, pendingPatchRef.current)
      setSaveError(null)
    } catch (err) {
      pendingPatchRef.current = mergeSelectionPatches(patch, pendingPatchRef.current ?? {})
      setDirty(true)
      setSaveError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const toggle = (patch: AgentSelectionPatch) => {
    setDisabled((current) => applySelectionPatch(current, patch))
    if (patch.model !== undefined) setModel(patch.model ?? null)
    pendingPatchRef.current = mergeSelectionPatches(pendingPatchRef.current ?? {}, patch)
    setDirty(true)
  }

  const decide = async (
    patch: AgentSelectionPatch,
    options: StandaloneSelectionWriteOptions = {},
  ) => {
    const apply = options.apply ?? 'now'
    const submittedPatch = mergeSelectionPatches(pendingPatchRef.current ?? {}, patch)
    pendingPatchRef.current = null
    if (apply === 'now') {
      setDisabled((current) => applySelectionPatch(current, patch))
      if (patch.model !== undefined) setModel(patch.model ?? null)
    }
    setSaving(true)
    try {
      const response = await runtime.submitUpdate(submittedPatch, options)
      applyResponseSelection(response, pendingPatchRef.current)
      setSaveError(null)
    } catch (err) {
      pendingPatchRef.current = mergeSelectionPatches(submittedPatch, pendingPatchRef.current ?? {})
      setDirty(true)
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
        // A `capabilities.open` scene command may carry spotlight targets.
        toolSpotlight: options.spotlight ?? null,
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
        dirty,
        saving,
        saveError,
        load,
        toggle,
        save: () => { void flush() },
        decide,
      },
      connections: {
        available: () => Boolean(runtime.openConnections),
        /* The bare "Manage connections" row passes NO consent payload — the
         * open must still fire (hub settings surface / plain deep link). A
         * consent-less call silently dropped here is exactly a dead row. */
        open: (_source: string, consent?: ConnectionsConsentOpen) => {
          runtime.openConnections?.(consent)
        },
      },
    }
    return vm as unknown as ChatViewModel
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, error, agent, inventory, disabled, model, cachePolicy, pending, dirty, saving, saveError, options.spotlight])
}
