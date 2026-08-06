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

function AgentRow({
  agent,
  onSelect,
  onDelete,
}: {
  agent: AgentSummary
  onSelect: (agentId: string) => void
  onDelete: (agentId: string) => void
}) {
  const hasError = Boolean(agent.error)

  return (
    <tr
      onClick={() => onSelect(agent.agent_id)}
      className="group border-b border-neutral-100 last:border-0 cursor-pointer hover:bg-neutral-50 transition-colors"
    >
      <td className="py-3 pl-6 pr-4 align-top">
        <div className="flex items-center gap-2">
          <StatusDot tone={hasError ? 'danger' : 'success'} />
          <span className="font-medium text-neutral-900">{agent.agent_id}</span>
        </div>
      </td>
      <td className="py-3 pr-4 align-top max-w-xl">
        <p className="text-sm text-neutral-500 leading-relaxed line-clamp-2">{agent.purpose || '—'}</p>
        <div className="flex items-center gap-1.5 flex-wrap mt-1.5">
          {hasError && <Badge tone="danger" uppercase>error</Badge>}
          {agent.pipeline && agent.pipeline.length > 0 && (
            <Badge mono>{agent.pipeline.length} stage{agent.pipeline.length === 1 ? '' : 's'}</Badge>
          )}
          {agent.version && <Badge mono>v{agent.version}</Badge>}
          {agent.skills && agent.skills.length > 0 &&
            !(agent.skills.length === 1 && agent.skills[0] === agent.agent_id) && (
            <Badge tone="brand" mono>uses {agent.skills.join(', ')}</Badge>
          )}
          {agent.routable === false && <Badge tone="warning" uppercase>not routable</Badge>}
          {agent.draft && <Badge tone="warning" uppercase>draft — needs review</Badge>}
        </div>
      </td>
      <td className="py-3 pl-4 pr-6 align-top text-right">
        <DangerIconButton
          label={`Delete agent "${agent.agent_id}"`}
          onClick={(e) => {
            e.stopPropagation()
            onDelete(agent.agent_id)
          }}
          className="opacity-0 group-hover:opacity-100"
        >
          <TrashIcon />
        </DangerIconButton>
      </td>
    </tr>
  )
}

function RowSkeleton() {
  return (
    <tr className="border-b border-neutral-100 last:border-0">
      <td className="py-4 pl-6 pr-4">
        <Skeleton className="h-4 w-32" />
      </td>
      <td className="py-4 pr-4">
        <Skeleton className="h-3 w-full max-w-md mb-1.5" />
        <Skeleton className="h-3 w-2/3 max-w-xs" />
      </td>
      <td className="py-4 pl-4 pr-6" />
    </tr>
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
      <div className="flex items-center justify-between px-6 py-5 border-b border-neutral-200 shrink-0 bg-white">
        <h1 className="text-lg font-semibold text-neutral-900">Agents</h1>
        <Button onClick={onNewAgent}>
          <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14M5 12h14" />
          </svg>
          New Agent
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {!loading && agents.length > 0 && (
          <div className="flex items-center justify-between mb-4">
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
            <span className="text-xs text-neutral-500">
              Showing {filtered.length} of {agents.length} agent{agents.length === 1 ? '' : 's'}
            </span>
          </div>
        )}

        {loading && (
          <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden" aria-busy="true" aria-label="Loading agents">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
                  <th className="py-2.5 pl-6 pr-4 font-semibold">Name</th>
                  <th className="py-2.5 pr-4 font-semibold">Description</th>
                  <th className="py-2.5 pl-4 pr-6" />
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 6 }).map((_, i) => (
                  <RowSkeleton key={i} />
                ))}
              </tbody>
            </table>
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
          <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-200 text-left text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
                  <th className="py-2.5 pl-6 pr-4 font-semibold">Name</th>
                  <th className="py-2.5 pr-4 font-semibold">Description</th>
                  <th className="py-2.5 pl-4 pr-6" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((agent) => (
                  <AgentRow key={agent.agent_id} agent={agent} onSelect={onSelect} onDelete={onDelete} />
                ))}
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={3} className="py-8 text-center text-sm text-neutral-400">
                      No agents match "{query}".
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
