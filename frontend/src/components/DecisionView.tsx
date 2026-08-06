import { useState } from 'react'
import type { ReactNode } from 'react'
import { api, ApiRequestError } from '../api'
import { Button, SegmentedControl } from './ui'

// Structured-card port of agent_platform/explainability/decision_record.py's
// render_markdown() — that function is the ground truth for "what exists to
// show" even though nothing serves it as markdown over HTTP today. Every
// section is independently hidden when its data is empty (a card UI, unlike
// a markdown doc, should never show an empty section header).

interface Gate {
  id: string
  description: string
  actual: unknown
  passed: boolean
}

interface Factor {
  id: string
  actual: unknown
  band_score: number
  weight: number
  contribution: number
}

interface ScoreCategory {
  value: number
  factors: Factor[]
}

interface Product {
  id: string
  name: string
  reason: string
}

interface Rationale {
  summary?: string
  selection_reason?: string
  customer_proposal?: string
  degraded?: boolean
  strengths?: { point: string; evidence_key: string }[]
  risks?: { point: string; evidence_key: string }[]
  next_best_action?: string
  confidence?: number
  product_rationale?: Record<string, string>
}

interface ErrorInfo {
  stage: string
  type: string
  message: string
}

interface SkillBreakdownEntry {
  skill_id: string
  outcome: string
  reason: string
  composite_score: number | null
}

const OUTCOME_STYLES: Record<string, string> = {
  QUALIFIED: 'bg-emerald-50 border-emerald-300 text-emerald-700',
  NOT_QUALIFIED: 'bg-red-50 border-red-300 text-red-700',
  NEEDS_HUMAN_REVIEW: 'bg-amber-50 border-amber-300 text-amber-700',
  CONDITIONALLY_QUALIFIED: 'bg-brand-50 border-brand-300 text-brand-700',
}
const DEFAULT_OUTCOME_STYLE = 'bg-neutral-100 border-neutral-300 text-neutral-700'

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h4 className="text-xs font-medium text-neutral-500 mb-1.5">{title}</h4>
      {children}
    </div>
  )
}

interface RoutingInfo {
  chosenAgentId: string | null
  confidence: number | null
  reasoning: string | null
  status: string
}

interface DecisionViewProps {
  decision: Record<string, unknown> | null
  explanation: Record<string, unknown> | null
  routingInfo?: RoutingInfo
}

export function DecisionView({ decision, explanation, routingInfo }: DecisionViewProps) {
  const [viewMode, setViewMode] = useState<'cards' | 'markdown'>('cards')
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [markdownLoading, setMarkdownLoading] = useState(false)
  const [markdownError, setMarkdownError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const handleViewMarkdown = async () => {
    setViewMode('markdown')
    if (markdown !== null || !explanation) return
    setMarkdownLoading(true)
    setMarkdownError(null)
    try {
      const res = await api.renderExplanationMarkdown(explanation)
      setMarkdown(res.markdown)
    } catch (err) {
      setMarkdownError(err instanceof ApiRequestError ? err.message : String(err))
    } finally {
      setMarkdownLoading(false)
    }
  }

  const handleCopy = () => {
    if (!markdown) return
    navigator.clipboard.writeText(markdown).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  const exp = explanation ?? {}
  const dec = decision ?? {}
  const isQualificationShaped = 'outcome' in dec && 'reason' in dec && 'composite_score' in dec

  const gates = (exp.gates as Gate[] | undefined) ?? []
  const scores = (exp.scores as Record<string, ScoreCategory> | undefined) ?? {}
  const products = (exp.product_recommendations as Product[] | undefined) ?? []
  const hitl = (exp.hitl as { triggered?: boolean; reasons?: string[] } | undefined) ?? {}
  const rationale = (exp.llm_rationale as Rationale | undefined) ?? undefined
  const error = (exp.error as ErrorInfo | undefined) ?? undefined
  const ruleResults = exp.rule_results as Record<string, unknown> | undefined
  const skillsLoaded = (exp.skills_loaded as string[] | undefined) ?? []
  const skillLoadingReasoning = (exp.skill_loading_reasoning as string | undefined) ?? undefined
  const skillBreakdown = (exp.skill_breakdown as SkillBreakdownEntry[] | undefined) ?? []

  const hasDecision = Object.keys(dec).length > 0
  const alreadyRendered = gates.length > 0 || Object.keys(scores).length > 0 || products.length > 0
  const showRuleResultsFallback = !alreadyRendered && ruleResults && Object.keys(ruleResults).length > 0

  const nothingToShow = !hasDecision && gates.length === 0 && Object.keys(scores).length === 0 &&
    products.length === 0 && !rationale && !showRuleResultsFallback && !error

  return (
    <div className="flex flex-col gap-4">
      {explanation && (
        <div className="flex justify-end">
          <SegmentedControl
            options={[
              { value: 'cards', label: 'Cards' },
              { value: 'markdown', label: 'Markdown' },
            ]}
            value={viewMode}
            onChange={(mode) => (mode === 'markdown' ? handleViewMarkdown() : setViewMode(mode))}
          />
        </div>
      )}

      {viewMode === 'markdown' ? (
        <div className="flex flex-col gap-2">
          {markdownLoading && <p className="text-xs text-neutral-500">Rendering…</p>}
          {markdownError && (
            <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{markdownError}</p>
          )}
          {markdown && (
            <div className="relative">
              <Button
                variant="secondary"
                onClick={handleCopy}
                className="absolute top-2 right-2 !px-2 !py-1 text-[11px]"
              >
                {copied ? 'Copied' : 'Copy'}
              </Button>
              <pre className="text-[11px] leading-relaxed text-neutral-700 font-mono whitespace-pre-wrap bg-neutral-50 border border-neutral-200 rounded-lg p-3 pr-16 overflow-x-auto">
                {markdown}
              </pre>
            </div>
          )}
        </div>
      ) : (
        <>
      {routingInfo && (
        <div className="text-xs bg-brand-50 border border-brand-200 rounded-lg p-3 text-brand-800">
          {routingInfo.chosenAgentId ? (
            <>
              Routed to <span className="font-mono">{routingInfo.chosenAgentId}</span>
              {routingInfo.confidence !== null && <> — confidence {routingInfo.confidence}</>}
              {routingInfo.reasoning && <div className="text-brand-700/80 mt-1">{routingInfo.reasoning}</div>}
            </>
          ) : (
            <span className="text-brand-700/80">{routingInfo.reasoning ?? routingInfo.status}</span>
          )}
        </div>
      )}

      {skillsLoaded.length > 1 && (
        <div className="text-xs bg-brand-50 border border-brand-200 rounded-lg p-3 text-brand-800">
          Skills loaded:{' '}
          {skillsLoaded.map((id, i) => (
            <span key={id} className="font-mono">
              {i > 0 && ', '}
              {id}
            </span>
          ))}
          {skillLoadingReasoning && <div className="text-brand-700/80 mt-1">{skillLoadingReasoning}</div>}
        </div>
      )}

      {skillBreakdown.length > 1 && (
        <div className="text-xs bg-neutral-50 border border-neutral-200 rounded-lg p-3">
          <div className="font-medium text-neutral-600 mb-1.5">Skills consulted</div>
          <div className="flex flex-col gap-1.5">
            {skillBreakdown.map((entry) => (
              <div key={entry.skill_id} className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-neutral-700">{entry.skill_id}</span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded border ${OUTCOME_STYLES[entry.outcome] ?? DEFAULT_OUTCOME_STYLE}`}
                >
                  {entry.outcome}
                </span>
                {entry.composite_score !== null && (
                  <span className="text-neutral-400">score {entry.composite_score}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {hasDecision && (
        <div>
          {isQualificationShaped ? (
            <div
              className={`inline-flex items-center gap-2 text-sm font-semibold rounded-lg border px-3 py-1.5 ${
                OUTCOME_STYLES[String(dec.outcome)] ?? DEFAULT_OUTCOME_STYLE
              }`}
            >
              {String(dec.outcome)}
            </div>
          ) : (
            <div className="flex flex-col gap-1 text-sm">
              {Object.entries(dec).map(([key, value]) => (
                <div key={key}>
                  <span className="text-neutral-500">{key.replace(/_/g, ' ')}:</span>{' '}
                  <span className="text-neutral-800">{String(value)}</span>
                </div>
              ))}
            </div>
          )}
          {isQualificationShaped && (
            <p className="text-xs text-neutral-500 mt-2 leading-relaxed">{String(dec.reason)}</p>
          )}
        </div>
      )}

      {hitl.triggered && (
        <div className="text-xs bg-amber-50 border border-amber-200 rounded-lg p-3 text-amber-800">
          Human review required — {(hitl.reasons ?? []).join('; ')}
        </div>
      )}

      {gates.length > 0 && (
        <Card title="Eligibility gates">
          <div className="flex flex-col gap-1">
            {gates.map((gate) => (
              <div key={gate.id} className="text-xs flex items-start gap-1.5">
                <span className={gate.passed ? 'text-emerald-600' : 'text-red-600'}>
                  {gate.passed ? '✓' : '✗'}
                </span>
                <span className="text-neutral-700">
                  {gate.id}
                  <span className="text-neutral-500"> — {gate.description} (actual: {String(gate.actual)})</span>
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {Object.keys(scores).length > 0 && (
        <Card title="Category scores">
          <div className="flex flex-col gap-2">
            {Object.entries(scores).map(([category, result]) => (
              <div key={category} className="text-xs bg-neutral-50 border border-neutral-200 rounded-lg p-2.5">
                <div className="text-neutral-800 font-medium mb-1">
                  {category} — {result.value}
                </div>
                {result.factors?.map((factor) => (
                  <div key={factor.id} className="text-neutral-500 pl-2">
                    {factor.id}: actual={String(factor.actual)} → band={factor.band_score}, weight={factor.weight},
                    contribution={factor.contribution}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </Card>
      )}

      {products.length > 0 && (
        <Card title="Recommended products">
          <div className="flex flex-col gap-1.5">
            {products.map((product) => (
              <div key={product.id} className="text-xs">
                <span className="text-neutral-800 font-medium">{product.name}</span>
                <span className="text-neutral-500">
                  {' '}
                  — {rationale?.product_rationale?.[product.id] ?? product.reason}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {rationale && (rationale.summary || rationale.selection_reason || rationale.customer_proposal) && (
        <Card title="Rationale">
          <p className="text-xs text-neutral-700 leading-relaxed">
            {rationale.summary || rationale.selection_reason || rationale.customer_proposal}
          </p>
          {rationale.degraded && (
            <p className="text-[11px] text-amber-700 mt-1">
              (deterministic fallback — LLM rationale unavailable)
            </p>
          )}
          {rationale.strengths && rationale.strengths.length > 0 && (
            <div className="mt-2">
              <div className="text-[11px] text-neutral-500 uppercase tracking-wide mb-0.5">Strengths</div>
              {rationale.strengths.map((s, i) => (
                <div key={i} className="text-xs text-neutral-700">
                  • {s.point} <span className="text-neutral-400">(evidence: {s.evidence_key})</span>
                </div>
              ))}
            </div>
          )}
          {rationale.risks && rationale.risks.length > 0 && (
            <div className="mt-2">
              <div className="text-[11px] text-neutral-500 uppercase tracking-wide mb-0.5">Risks</div>
              {rationale.risks.map((r, i) => (
                <div key={i} className="text-xs text-neutral-700">
                  • {r.point} <span className="text-neutral-400">(evidence: {r.evidence_key})</span>
                </div>
              ))}
            </div>
          )}
          {rationale.next_best_action && (
            <p className="text-xs text-neutral-700 mt-2">
              <span className="text-neutral-500">Next best action:</span> {rationale.next_best_action}
            </p>
          )}
          {rationale.confidence !== undefined && (
            <p className="text-xs text-neutral-500 mt-1">Confidence: {rationale.confidence}</p>
          )}
        </Card>
      )}

      {showRuleResultsFallback && (
        <Card title="Computed facts">
          <pre className="text-xs bg-neutral-50 border border-neutral-200 rounded-lg p-3 overflow-x-auto text-neutral-700">
            {JSON.stringify(ruleResults, null, 2)}
          </pre>
        </Card>
      )}

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
          Stage <span className="font-mono">{error.stage}</span> failed: {error.type}: {error.message}
        </div>
      )}

      {nothingToShow && (
        <p className="text-xs text-neutral-400">This run produced no decision-shaped output to display.</p>
      )}
        </>
      )}
    </div>
  )
}
