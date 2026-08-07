import { useMemo, useState } from 'react'
import type { AgentSummary } from '../types'
import { Badge, Button, DangerIconButton, Skeleton, StatusDot, TrashIcon } from './ui'

interface DashboardProps {
  agents: AgentSummary[]
  loading: boolean
  onSelect: (agentId: string) => void
  onNewAgent: () => void
  onDelete: (agentId: string) => void
}

function AgentCard({
  agent,
  onSelect,
  onDelete,
}: {
  agent: AgentSummary
  onSelect: (agentId: string) => void
  onDelete: (agentId: string) => void
}) {
  const hasError = Boolean(agent.error)
  const usesOtherSkills =
    agent.skills && agent.skills.length > 0 && !(agent.skills.length === 1 && agent.skills[0] === agent.agent_id)

  return (
    <div
      onClick={() => onSelect(agent.agent_id)}
      className="group flex items-start gap-4 rounded-xl border border-neutral-200 bg-white px-5 py-4 cursor-pointer transition-all hover:border-brand-300 hover:shadow-[0_1px_0_0_rgba(0,0,0,0.02),0_4px_16px_-4px_rgba(18,42,74,0.08)]"
    >
      <StatusDot tone={hasError ? 'danger' : 'success'} className="mt-1.5" />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-1">
          <h3 className="font-semibold text-neutral-900 truncate">{agent.agent_id}</h3>
          {agent.version && <span className="text-[11px] text-neutral-400 font-mono shrink-0">v{agent.version}</span>}
        </div>
        <p className="text-sm text-neutral-500 leading-relaxed line-clamp-2 mb-2.5">{agent.purpose || '—'}</p>

        <div className="flex items-center gap-1.5 flex-wrap">
          {hasError && <Badge tone="danger" uppercase>error</Badge>}
          {agent.draft && <Badge tone="warning" uppercase>draft</Badge>}
          {agent.routable === false && <Badge tone="warning" uppercase>not routable</Badge>}
          {usesOtherSkills && <Badge tone="brand" mono>{agent.skills!.join(', ')}</Badge>}
          {agent.pipeline && agent.pipeline.length > 0 && (
            <Badge mono>{agent.pipeline.length} stage{agent.pipeline.length === 1 ? '' : 's'}</Badge>
          )}
        </div>
      </div>

      <DangerIconButton
        label={`Delete agent "${agent.agent_id}"`}
        onClick={(e) => {
          e.stopPropagation()
          onDelete(agent.agent_id)
        }}
        className="opacity-0 group-hover:opacity-100 shrink-0"
      >
        <TrashIcon />
      </DangerIconButton>
    </div>
  )
}

function CardSkeleton() {
  return (
    <div className="flex items-start gap-4 rounded-xl border border-neutral-200 bg-white px-5 py-4">
      <Skeleton className="w-2 h-2 rounded-full mt-2" />
      <div className="flex-1">
        <Skeleton className="h-4 w-40 mb-2.5" />
        <Skeleton className="h-3 w-full max-w-md mb-1.5" />
        <Skeleton className="h-3 w-2/3 max-w-xs" />
      </div>
    </div>
  )
}

export function Dashboard({ agents, loading, onSelect, onNewAgent, onDelete }: DashboardProps) {
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return agents
    return agents.filter(
      (a) => a.agent_id.toLowerCase().includes(q) || (a.purpose ?? '').toLowerCase().includes(q),
    )
  }, [agents, query])

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-8 py-6 border-b border-neutral-200 shrink-0 bg-white">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">Agents</h1>
          <p className="text-sm text-neutral-400 mt-0.5">Every agent this platform can run, in one place.</p>
        </div>
        <Button onClick={onNewAgent}>
          <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14M5 12h14" />
          </svg>
          New Agent
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {!loading && agents.length > 0 && (
          <div className="flex items-center justify-between mb-5">
            <div className="relative w-72">
              <svg
                viewBox="0 0 24 24"
                className="w-4 h-4 text-neutral-400 absolute left-3 top-1/2 -translate-y-1/2"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="11" cy="11" r="7" />
                <path strokeLinecap="round" d="m20 20-3.5-3.5" />
              </svg>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search agents…"
                className="w-full rounded-md border border-neutral-300 bg-white text-sm pl-9 pr-3 py-2 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
              />
            </div>
            <span className="text-xs text-neutral-400">
              {filtered.length} of {agents.length} agent{agents.length === 1 ? '' : 's'}
            </span>
          </div>
        )}

        {loading && (
          <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading agents">
            {Array.from({ length: 5 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        )}

        {!loading && agents.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center gap-3 text-center">
            <div className="w-12 h-12 rounded-xl bg-brand-50 border border-brand-100 flex items-center justify-center">
              <svg viewBox="0 0 24 24" className="w-6 h-6 text-brand-500" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z" />
              </svg>
            </div>
            <div>
              <p className="text-sm text-neutral-600">No agents yet</p>
              <p className="text-xs text-neutral-400 mt-1">Create your first agent to get started.</p>
            </div>
            <Button onClick={onNewAgent} className="mt-1">
              + New Agent
            </Button>
          </div>
        )}

        {!loading && agents.length > 0 && (
          <div className="flex flex-col gap-3">
            {filtered.map((agent) => (
              <AgentCard key={agent.agent_id} agent={agent} onSelect={onSelect} onDelete={onDelete} />
            ))}
            {filtered.length === 0 && (
              <p className="py-12 text-center text-sm text-neutral-400">No agents match "{query}".</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
