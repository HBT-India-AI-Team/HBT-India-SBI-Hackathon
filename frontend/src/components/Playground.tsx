import { useEffect, useState } from 'react'
import { SchemaForm } from './SchemaForm'
import { DecisionView } from './DecisionView'
import { CodeEditor } from './CodeEditor'
import { ChatWindow } from './ChatWindow'
import { Button, ChoiceCard, SegmentedControl } from './ui'
import { api, ApiRequestError } from '../api'
import type { AgentSummary, InputMode, StageTraceEntry } from '../types'

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
  stageTrace?: StageTraceEntry[]
}

/** A stage's reasoning evidence. Everything shown here came back from the
 *  model itself — the `thinking` block is its actual chain of thought, not a
 *  summary generated afterwards. Stages that produced no LLM call render
 *  nothing extra, so an empty panel means "this step didn't reason", which is
 *  itself the honest answer. */
function StageDetailView({ detail }: { detail: NonNullable<StageTraceEntry['detail']> }) {
  const [showThinking, setShowThinking] = useState(false)
  const { model, prompt_tokens, completion_tokens, done_reason, thinking, tool_calls, llm_error } = detail

  const stats = [
    model && { label: 'model', value: model },
    prompt_tokens != null && { label: 'in', value: `${prompt_tokens.toLocaleString()} tok` },
    completion_tokens != null && { label: 'out', value: `${completion_tokens.toLocaleString()} tok` },
    done_reason && done_reason !== 'stop' && { label: 'stopped', value: done_reason },
  ].filter(Boolean) as { label: string; value: string }[]

  return (
    <div className="mt-1.5 flex flex-col gap-1.5">
      {stats.length > 0 && (
        <div className="flex flex-wrap gap-x-2.5 gap-y-1">
          {stats.map((s) => (
            <span key={s.label} className="text-[10px] font-mono text-neutral-400">
              <span className="text-neutral-300">{s.label}</span> {s.value}
            </span>
          ))}
        </div>
      )}

      {llm_error && (
        <p className="text-[11px] text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1">
          Model call failed: {llm_error}
        </p>
      )}

      {tool_calls && tool_calls.length > 0 && (
        <ul className="flex flex-col gap-1">
          {tool_calls.map((call, i) => (
            <li key={i} className="text-[10px] font-mono bg-neutral-50 border border-neutral-200 rounded px-2 py-1">
              <div className="text-brand-700 break-all">
                {call.name}({JSON.stringify(call.arguments)})
              </div>
              <div className="text-neutral-500 break-all mt-0.5">→ {JSON.stringify(call.result)}</div>
            </li>
          ))}
        </ul>
      )}

      {thinking && (
        <div>
          <button
            type="button"
            onClick={() => setShowThinking((v) => !v)}
            className="text-[11px] text-brand-600 hover:text-brand-700 cursor-pointer flex items-center gap-1 transition-colors"
          >
            <span className={`inline-block transition-transform ${showThinking ? 'rotate-90' : ''}`}>▸</span>
            Model's reasoning ({thinking.length.toLocaleString()} chars)
          </button>
          {showThinking && (
            <pre className="mt-1 text-[10px] leading-relaxed text-neutral-600 bg-neutral-50 border border-neutral-200 rounded p-2 max-h-64 overflow-y-auto whitespace-pre-wrap font-mono">
              {thinking}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

function ObservationPanel({ stageTrace, running }: { stageTrace?: StageTraceEntry[]; running: boolean }) {
  if (running) {
    return <p className="text-xs text-neutral-400 italic">Running…</p>
  }
  if (!stageTrace || stageTrace.length === 0) {
    return <p className="text-xs text-neutral-400">Run the agent to see its step-by-step trace here.</p>
  }
  const totalMs = stageTrace.reduce((sum, t) => sum + t.duration_ms, 0)
  const totalTokens = stageTrace.reduce(
    (sum, t) => sum + (t.detail?.prompt_tokens ?? 0) + (t.detail?.completion_tokens ?? 0),
    0,
  )
  return (
    <div className="flex flex-col gap-2">
      <p className="text-[11px] text-neutral-400">
        {stageTrace.length} steps, {Math.round(totalMs)}ms total
        {totalTokens > 0 && ` · ${totalTokens.toLocaleString()} tokens`}
      </p>
      <ul className="flex flex-col gap-2">
        {stageTrace.map((t, i) => (
          <li key={i} className="border-l-2 border-neutral-200 pl-2.5">
            <div className="flex items-center gap-1.5 text-xs font-mono">
              <span className={t.status === 'error' ? 'text-red-500' : 'text-emerald-500'}>
                {t.status === 'ok' ? '✓' : t.status === 'error' ? '✕' : '·'}
              </span>
              <span className="text-neutral-700">{t.stage}</span>
              <span className="text-neutral-300 ml-auto">{Math.round(t.duration_ms)}ms</span>
            </div>
            <p className="text-[11px] text-neutral-400 mt-0.5">{t.summary}</p>
            {t.detail && Object.keys(t.detail).length > 0 && <StageDetailView detail={t.detail} />}
          </li>
        ))}
      </ul>
    </div>
  )
}

const INPUT_MODE_OPTIONS: { value: InputMode; label: string }[] = [
  { value: 'chat', label: 'Chat' },
  { value: 'form', label: 'Form' },
  { value: 'json', label: 'JSON' },
  { value: 'trigger', label: 'Trigger' },
  { value: 'file', label: 'File' },
]

// Demo-specific handoff: fin_health's real result becomes proposal_generator's trigger
// context. Hardcoded rather than a generic "next agent" concept, since that doesn't exist
// in AgentDefinition yet — this is the one pairing the tender workflow actually needs today.
const PROPOSAL_ELIGIBLE_OUTCOMES = new Set(['QUALIFIED', 'CONDITIONALLY_QUALIFIED'])

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
  const [inputMode, setInputModeState] = useState<InputMode>('chat')
  const [modeSaveError, setModeSaveError] = useState<string | null>(null)
  const [formValues, setFormValues] = useState<Record<string, unknown> | null>({})
  const [autoInputText, setAutoInputText] = useState('{\n  \n}')
  const [sampleFillVersion, setSampleFillVersion] = useState(0)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  // Set by "Send to Proposal" — a real fin_health result carried forward as proposal_generator's
  // trigger context, overriding its static demo_sample_input for this one run.
  const [pendingTriggerContext, setPendingTriggerContext] = useState<Record<string, unknown> | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<RunOutcome | null>(null)

  const usableAgents = agents.filter((a) => !a.error)
  const effectiveAgentId = lockedAgentId ?? (mode === 'manual' ? manualAgentId : null)
  const effectiveAgent = effectiveAgentId ? usableAgents.find((a) => a.agent_id === effectiveAgentId) : undefined

  // Follow the agent's own declared default whenever the selected agent
  // changes — but once someone picks a different mode for this agent below,
  // that choice is persisted (see handleInputModeChange) and becomes the
  // new default on the next visit, not just this session.
  useEffect(() => {
    setInputModeState(effectiveAgent?.input_mode ?? 'chat')
    setModeSaveError(null)
    setOutcome(null)
    setError(null)
  }, [effectiveAgentId, effectiveAgent?.input_mode])

  const handleInputModeChange = (next: InputMode) => {
    setInputModeState(next)
    if (!effectiveAgentId) return
    setModeSaveError(null)
    api.setInputMode(effectiveAgentId, next).catch((err) => {
      setModeSaveError(err instanceof ApiRequestError ? err.message : String(err))
    })
  }

  const buildPayload = (): Record<string, unknown> | null => {
    // A trigger agent has no input UI by definition — it runs from its baked-in demo
    // context (its demo_sample_input) when one is declared, exactly as a real time/event
    // trigger would carry fixed context, not {} unless the agent truly takes nothing.
    // pendingTriggerContext (a real prior run's result, carried over via "Send to
    // Proposal") takes priority over the agent's own static sample.
    if (inputMode === 'trigger') return pendingTriggerContext ?? effectiveAgent?.demo_sample_input ?? {}
    if (inputMode === 'form') return formValues
    // 'json' (and 'chat' never reaches here — ChatWindow handles its own calls)
    try {
      return autoInputText.trim() === '' ? {} : JSON.parse(autoInputText)
    } catch (err) {
      setError(`Input is not valid JSON: ${err instanceof Error ? err.message : String(err)}`)
      return null
    }
  }

  const runDirect = async (agentId: string) => {
    const payload = buildPayload()
    if (payload === null) return
    const res = await api.testRunAgent(agentId, payload)
    if (res.error) {
      setOutcome({
        kind: 'failed',
        decision: null,
        explanation: null,
        message: `Stage ${res.error.stage} failed: ${res.error.type}: ${res.error.message}`,
        stageTrace: res.stage_trace,
      })
    } else {
      setOutcome({ kind: 'success', decision: res.decision, explanation: res.explanation, stageTrace: res.stage_trace })
    }
  }

  const runFile = async (agentId: string) => {
    if (!selectedFile) {
      setError('Choose a file first.')
      return
    }
    const res = await api.testRunAgentFile(agentId, selectedFile)
    if (res.error) {
      setOutcome({
        kind: 'failed',
        decision: null,
        explanation: null,
        message: `Stage ${res.error.stage} failed: ${res.error.type}: ${res.error.message}`,
        stageTrace: res.stage_trace,
      })
    } else {
      setOutcome({ kind: 'success', decision: res.decision, explanation: res.explanation, stageTrace: res.stage_trace })
    }
  }

  // Sends fin_health's just-completed real result to proposal_generator as fixed trigger
  // context — the "click a button, get the proposal" handoff. Only offered for outcomes
  // that should ever reach a proposal (never a Rejected one).
  const sendToProposal = () => {
    const decision = outcome?.decision as { outcome?: string; composite_score?: number } | null
    const inputSummary = outcome?.explanation?.input_summary as { evidence?: Record<string, unknown> } | undefined
    const evidence = inputSummary?.evidence ?? {}
    const context = {
      evidence: {
        ...evidence,
        fin_health_outcome: decision?.outcome,
        fin_health_score: decision?.composite_score,
      },
    }
    setMode('manual')
    setManualAgentId('proposal_generator')
    setPendingTriggerContext(context)
    setOutcome(null)
    setError(null)
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
      if (effectiveAgentId && inputMode === 'file') {
        await runFile(effectiveAgentId)
      } else if (effectiveAgentId) {
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

  const canRun = effectiveAgentId
    ? (inputMode !== 'form' || formValues !== null) && (inputMode !== 'file' || selectedFile !== null)
    : true
  const showChat = effectiveAgentId && effectiveAgent && inputMode === 'chat'

  return (
    <div className="flex flex-col h-full gap-4 min-h-0">
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
          onChange={(e) => {
            setManualAgentId(e.target.value || null)
            setPendingTriggerContext(null)
            setSelectedFile(null)
          }}
        >
          <option value="">Select an agent…</option>
          {usableAgents.map((a) => (
            <option key={a.agent_id} value={a.agent_id}>
              {a.agent_id}
            </option>
          ))}
        </select>
      )}

      {effectiveAgentId && effectiveAgent && (
        <div>
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-neutral-700">{effectiveAgentId}</h3>
            <SegmentedControl options={INPUT_MODE_OPTIONS} value={inputMode} onChange={handleInputModeChange} />
          </div>
          {modeSaveError && <p className="text-[11px] text-red-600 mt-1">Couldn't save mode: {modeSaveError}</p>}
        </div>
      )}

      {showChat ? (
        <div className="flex-1 min-h-[420px]">
          <ChatWindow agentId={effectiveAgentId} demoSampleInput={effectiveAgent.demo_sample_input} />
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1 min-h-0">
          {/* Column 1 — Input */}
          <div className="flex flex-col gap-3 min-h-0 border border-neutral-200 rounded-lg bg-white p-4 overflow-y-auto">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                {effectiveAgentId ? 'Input' : 'Input (raw JSON — target agent not yet known)'}
              </h3>
              {effectiveAgent?.demo_sample_input && (inputMode === 'form' || inputMode === 'json') && (
                <button
                  type="button"
                  onClick={() => {
                    const sample = effectiveAgent.demo_sample_input as Record<string, unknown>
                    if (inputMode === 'form') {
                      setSampleFillVersion((v) => v + 1)
                    } else {
                      setAutoInputText(JSON.stringify(sample, null, 2))
                    }
                  }}
                  className="text-xs font-medium text-brand-600 hover:text-brand-700 border border-brand-200 bg-brand-50 rounded-md px-3 py-1.5 cursor-pointer transition-colors"
                >
                  Fill sample data
                </button>
              )}
            </div>

            {effectiveAgentId && effectiveAgent ? (
              inputMode === 'trigger' ? (
                (pendingTriggerContext ?? effectiveAgent.demo_sample_input) ? (
                  <div className="flex flex-col gap-1.5">
                    <p className="text-xs text-neutral-400">
                      {pendingTriggerContext
                        ? 'Context carried over from the run you just sent here — not typed by a user, the way a real trigger would receive it. Click Run.'
                        : 'No input UI — this is the fixed context the trigger carries every run, the way a real time/event trigger would (not typed by a user). Click Run.'}
                    </p>
                    <div className="h-32 border border-neutral-200 rounded-lg overflow-hidden shadow-sm">
                      <CodeEditor
                        value={JSON.stringify(pendingTriggerContext ?? effectiveAgent.demo_sample_input, null, 2)}
                        language="json"
                        onChange={() => {}}
                        readOnly
                      />
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-neutral-400 border border-dashed border-neutral-300 rounded-lg p-4 text-center">
                    No input needed — this agent runs from a trigger. Click Run.
                  </p>
                )
              ) : inputMode === 'file' ? (
                <div className="flex flex-col gap-2">
                  <p className="text-xs text-neutral-400">
                    Upload the Excel report — it's parsed server-side into the evidence fields this
                    agent scores against.
                  </p>
                  <input
                    type="file"
                    accept=".xls,.xlsx"
                    onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
                    className="text-xs text-neutral-600 file:mr-3 file:rounded-md file:border-0 file:bg-brand-50 file:text-brand-700 file:text-xs file:font-medium file:px-3 file:py-1.5 file:cursor-pointer cursor-pointer"
                  />
                  {selectedFile && (
                    <p className="text-[11px] text-neutral-400">Selected: {selectedFile.name}</p>
                  )}
                </div>
              ) : inputMode === 'json' ? (
                <div className="h-32 border border-neutral-200 rounded-lg overflow-hidden shadow-sm">
                  <CodeEditor value={autoInputText} language="json" onChange={setAutoInputText} />
                </div>
              ) : (
                <SchemaForm
                  schema={effectiveAgent.input_schema}
                  onChange={setFormValues}
                  seedValues={effectiveAgent.demo_sample_input}
                  seedVersion={sampleFillVersion}
                />
              )
            ) : effectiveAgentId ? (
              <p className="text-xs text-neutral-400">Select an agent above to see its inputs.</p>
            ) : (
              <div className="h-32 border border-neutral-200 rounded-lg overflow-hidden shadow-sm">
                <CodeEditor value={autoInputText} language="json" onChange={setAutoInputText} />
              </div>
            )}

            {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>}

            <Button variant="success" size="sm" onClick={run} disabled={running || !canRun} className="mt-auto self-start">
              <RunIcon running={running} />
              {running ? 'Running… (~10-60s)' : 'Run'}
            </Button>
          </div>

          {/* Column 2 — AI Observation */}
          <div className="flex flex-col gap-2 min-h-0 border border-neutral-200 rounded-lg bg-white p-4 overflow-y-auto">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">AI Observation</h3>
            <ObservationPanel stageTrace={outcome?.stageTrace} running={running} />
          </div>

          {/* Column 3 — Output */}
          <div className="flex flex-col gap-3 min-h-0 border border-neutral-200 rounded-lg bg-white p-4 overflow-y-auto">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Output</h3>

            {!outcome && !running && (
              <p className="text-xs text-neutral-400">Results will appear here after you click Run.</p>
            )}

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
              <>
                <DecisionView decision={outcome.decision} explanation={outcome.explanation} routingInfo={outcome.routingInfo} />
                {effectiveAgentId === 'fin_health' &&
                  PROPOSAL_ELIGIBLE_OUTCOMES.has(String((outcome.decision as { outcome?: string } | null)?.outcome)) && (
                    <button
                      type="button"
                      onClick={sendToProposal}
                      className="self-start text-sm font-medium text-white bg-brand-600 hover:bg-brand-700 rounded-md px-4 py-2 cursor-pointer transition-colors"
                    >
                      Send to Proposal →
                    </button>
                  )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
