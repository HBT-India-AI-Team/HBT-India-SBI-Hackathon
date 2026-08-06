import { useState } from 'react'
import { SchemaForm } from './SchemaForm'
import { DecisionView } from './DecisionView'
import { CodeEditor } from './CodeEditor'
import { Button, ChoiceCard } from './ui'
import { api, ApiRequestError } from '../api'
import type { AgentSummary } from '../types'

interface PlaygroundProps {
  agents: AgentSummary[]
  /** When set, the agent is fixed (embedded-in-editor use) — no picker/auto-route toggle shown. */
  lockedAgentId?: string
}

type Mode = 'auto' | 'manual'

interface RunOutcome {
  kind: 'success' | 'needs_clarification' | 'failed'
  decision: Record<string, unknown> | null
  explanation: Record<string, unknown> | null
  message?: string
  routingInfo?: { chosenAgentId: string | null; confidence: number | null; reasoning: string | null; status: string }
}

function RunIcon({ running }: { running: boolean }) {
  return running ? (
    <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 animate-spin" fill="none" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="currentColor">
      <path d="M8 5.14v13.72a1 1 0 0 0 1.5.86l11-6.86a1 1 0 0 0 0-1.72l-11-6.86A1 1 0 0 0 8 5.14Z" />
    </svg>
  )
}

export function Playground({ agents, lockedAgentId }: PlaygroundProps) {
  const [mode, setMode] = useState<Mode>('auto')
  const [manualAgentId, setManualAgentId] = useState<string | null>(null)
  const [formValues, setFormValues] = useState<Record<string, unknown> | null>({})
  const [autoInputText, setAutoInputText] = useState('{\n  \n}')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<RunOutcome | null>(null)

  const usableAgents = agents.filter((a) => !a.error)
  const effectiveAgentId = lockedAgentId ?? (mode === 'manual' ? manualAgentId : null)
  const effectiveAgent = effectiveAgentId ? usableAgents.find((a) => a.agent_id === effectiveAgentId) : undefined

  const runDirect = async (agentId: string) => {
    if (formValues === null) return
    const res = await api.testRunAgent(agentId, formValues)
    if (res.error) {
      setOutcome({
        kind: 'failed',
        decision: null,
        explanation: null,
        message: `Stage ${res.error.stage} failed: ${res.error.type}: ${res.error.message}`,
      })
    } else {
      setOutcome({ kind: 'success', decision: res.decision, explanation: res.explanation })
    }
  }

  const runAuto = async () => {
    let parsed: Record<string, unknown>
    try {
      parsed = autoInputText.trim() === '' ? {} : JSON.parse(autoInputText)
    } catch (err) {
      setError(`Input is not valid JSON: ${err instanceof Error ? err.message : String(err)}`)
      return
    }
    const res = await api.runAgentRouter(parsed)
    const routingInfo = {
      chosenAgentId: res.chosen_agent_id,
      confidence: res.routing_confidence,
      reasoning: res.routing_reasoning,
      status: res.status,
    }
    if (res.status === 'COMPLETED') {
      setOutcome({ kind: 'success', decision: res.decision, explanation: res.explanation, routingInfo })
    } else if (res.status === 'NEEDS_CLARIFICATION') {
      setOutcome({ kind: 'needs_clarification', decision: null, explanation: null, routingInfo })
    } else {
      setOutcome({
        kind: 'failed',
        decision: null,
        explanation: null,
        routingInfo,
        message: res.error
          ? `Stage ${res.error.stage} failed: ${res.error.type}: ${res.error.message}`
          : `Routing failed (${res.status}).`,
      })
    }
  }

  const run = async () => {
    setRunning(true)
    setError(null)
    setOutcome(null)
    try {
      if (effectiveAgentId) {
        await runDirect(effectiveAgentId)
      } else {
        await runAuto()
      }
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : String(err))
    } finally {
      setRunning(false)
    }
  }

  const canRun = effectiveAgentId ? formValues !== null : true

  return (
    <div className="flex flex-col h-full gap-4 overflow-y-auto">
      {!lockedAgentId && (
        <div className="grid grid-cols-2 gap-2">
          <ChoiceCard
            selected={mode === 'auto'}
            onClick={() => setMode('auto')}
            title="Let the platform decide"
            subtitle="agent_router picks which agent handles this"
          />
          <ChoiceCard
            selected={mode === 'manual'}
            onClick={() => setMode('manual')}
            title="Pick an agent"
            subtitle="test one specific agent directly"
          />
        </div>
      )}

      {!lockedAgentId && mode === 'manual' && (
        <select
          className="w-full rounded-md border border-neutral-300 bg-white text-neutral-900 text-sm px-3 py-2 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
          value={manualAgentId ?? ''}
          onChange={(e) => setManualAgentId(e.target.value || null)}
        >
          <option value="">Select an agent…</option>
          {usableAgents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>
              {a.agent_id}
            </option>
          ))}
        </select>
      )}

      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-neutral-700">
          {effectiveAgentId ? `Input — ${effectiveAgentId}` : 'Input (raw JSON — target agent not yet known)'}
        </h3>
        <Button variant="success" size="sm" onClick={run} disabled={running || !canRun}>
          <RunIcon running={running} />
          {running ? 'Running… (~10-60s)' : 'Run'}
        </Button>
      </div>

      {effectiveAgentId ? (
        !effectiveAgent ? (
          <p className="text-xs text-neutral-400">Select an agent above to see its inputs.</p>
        ) : (
          <SchemaForm schema={effectiveAgent.input_schema} onChange={setFormValues} />
        )
      ) : (
        <div className="h-32 border border-neutral-200 rounded-lg overflow-hidden shadow-sm">
          <CodeEditor value={autoInputText} language="json" onChange={setAutoInputText} />
        </div>
      )}

      {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>}

      {outcome?.kind === 'needs_clarification' && (
        <div className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-3">
          Could not confidently determine which agent to use.
          {outcome.routingInfo?.reasoning && <div className="text-xs text-amber-700/80 mt-1">{outcome.routingInfo.reasoning}</div>}
        </div>
      )}

      {outcome?.kind === 'failed' && (
        <div className="flex flex-col gap-2">
          {outcome.routingInfo?.chosenAgentId && (
            <div className="text-xs text-neutral-500">
              Routed to <span className="font-mono">{outcome.routingInfo.chosenAgentId}</span>
            </div>
          )}
          <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
            {outcome.message ?? 'The run failed.'}
          </div>
        </div>
      )}

      {outcome?.kind === 'success' && (
        <DecisionView decision={outcome.decision} explanation={outcome.explanation} routingInfo={outcome.routingInfo} />
      )}
    </div>
  )
}
