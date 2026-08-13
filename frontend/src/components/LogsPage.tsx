import { useEffect, useState } from 'react'
import { api, ApiRequestError } from '../api'
import { Badge, Button, Skeleton } from './ui'
import type { OllamaCallLog, OllamaCallLogSummary } from '../types'

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const secs = Math.round(diffMs / 1000)
  if (secs < 60) return `${secs}s ago`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return new Date(iso).toLocaleString()
}

function CallRow({ call }: { call: OllamaCallLogSummary }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<OllamaCallLog | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  // Bodies are fetched on first expand, not with the list — see types.ts.
  // Once loaded they're kept, so collapsing and reopening is free.
  const toggle = () => {
    const opening = !expanded
    setExpanded(opening)
    if (!opening || detail) return
    setDetailError(null)
    api
      .getOllamaLogDetail(call.offset)
      .then(setDetail)
      .catch((err) => setDetailError(err instanceof ApiRequestError ? err.message : String(err)))
  }

  return (
    // shrink-0 is load-bearing: the parent is `flex flex-col` and `overflow-hidden`
    // here sets this item's automatic minimum size to 0 instead of min-content, so
    // with enough rows to overflow, flex-shrink squashed every row to its border —
    // the whole list rendered as hairlines with the text clipped inside.
    <div className="border border-neutral-200 rounded-lg bg-white overflow-hidden shrink-0">
      <button
        onClick={toggle}
        className="w-full flex items-center gap-3 px-4 py-3 text-left cursor-pointer hover:bg-neutral-50 transition-colors"
      >
        <Badge tone={call.ok ? 'success' : 'danger'} uppercase className="shrink-0">
          {call.ok ? 'ok' : 'failed'}
        </Badge>
        <span className="text-sm font-mono text-neutral-800 shrink-0">{call.model}</span>
        <span className="text-xs text-neutral-400 shrink-0">
          attempt {call.attempt}/{call.total_attempts}
        </span>
        <span className="text-xs text-neutral-400 shrink-0 font-mono">{(call.duration_ms / 1000).toFixed(1)}s</span>
        {call.error && <span className="text-xs text-red-600 truncate">{call.error}</span>}
        <span className="text-xs text-neutral-400 ml-auto shrink-0">{relativeTime(call.timestamp)}</span>
      </button>

      {expanded && (
        <div className="border-t border-neutral-200 p-4 flex flex-col gap-3 bg-neutral-50">
          {detailError && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{detailError}</p>
          )}

          {!detail && !detailError && <Skeleton className="h-24 w-full rounded-md" />}

          {detail && (
            <>
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-wide text-neutral-400 mb-1.5">
                  Request
                </label>
                <pre className="rounded-md border border-neutral-200 bg-neutral-900 text-neutral-100 text-xs font-mono p-3 overflow-x-auto max-h-72 overflow-y-auto">
                  {JSON.stringify(detail.request, null, 2)}
                </pre>
              </div>
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-wide text-neutral-400 mb-1.5">
                  {call.ok ? 'Response' : 'Error'}
                </label>
                <pre className="rounded-md border border-neutral-200 bg-neutral-900 text-neutral-100 text-xs font-mono p-3 overflow-x-auto max-h-72 overflow-y-auto">
                  {call.ok ? JSON.stringify(detail.response, null, 2) : detail.error}
                </pre>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export function LogsPage() {
  const [calls, setCalls] = useState<OllamaCallLogSummary[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [failuresOnly, setFailuresOnly] = useState(false)

  const load = () => {
    setLoading(true)
    setError(null)
    api
      .getOllamaLogs(200)
      .then((res) => setCalls(res.calls))
      .catch((err) => setError(err instanceof ApiRequestError ? err.message : String(err)))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const visible = calls?.filter((c) => !failuresOnly || !c.ok) ?? []

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-8 py-6 border-b border-neutral-200 shrink-0 bg-white">
        <div>
          <h1 className="text-xl font-semibold text-neutral-900">Ollama call log</h1>
          <p className="text-sm text-neutral-400 mt-0.5">
            Every request/response to the LLM, including failed retries — pulled from logs/ollama_calls.jsonl.
          </p>
        </div>
        <Button onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6 flex flex-col gap-3">
        <label className="flex items-center gap-2 text-sm text-neutral-600 mb-1 cursor-pointer w-fit">
          <input
            type="checkbox"
            checked={failuresOnly}
            onChange={(e) => setFailuresOnly(e.target.checked)}
            className="cursor-pointer"
          />
          Failures only
        </label>

        {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</p>}

        {loading && !calls && (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-lg" />
            ))}
          </div>
        )}

        {!loading && calls && calls.length === 0 && (
          <p className="text-sm text-neutral-400 text-center py-12">
            No calls logged yet — this fills in the first time an agent talks to the LLM.
          </p>
        )}

        {!loading && visible.length === 0 && calls && calls.length > 0 && (
          <p className="text-sm text-neutral-400 text-center py-12">No failures in the last {calls.length} calls.</p>
        )}

        {/* Keyed by offset, not index — each row now owns fetched body state,
            and an index key would hand it to a different call after a refresh. */}
        {visible.map((call) => (
          <CallRow key={call.offset} call={call} />
        ))}
      </div>
    </div>
  )
}
