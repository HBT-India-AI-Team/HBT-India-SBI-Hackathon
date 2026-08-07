import { useEffect, useState, type ReactNode } from 'react'
import { api, ApiRequestError } from '../api'
import { Badge, Button } from './ui'
import type { InputSchema } from '../types'

interface IntegratePanelProps {
  agentId: string
  inputSchema?: InputSchema
}

function exampleValue(type: string | undefined): unknown {
  switch (type) {
    case 'number':
    case 'integer':
      return 0
    case 'object':
      return {}
    case 'array':
      return []
    default:
      return 'example'
  }
}

function exampleBody(schema?: InputSchema): Record<string, unknown> {
  const properties = schema?.properties ?? {}
  const keys = Object.keys(properties)
  if (keys.length === 0) return { field: 'example' }
  return Object.fromEntries(keys.map((key) => [key, exampleValue(properties[key].type)]))
}

function buildIframeSnippet(agentId: string): string {
  return `<iframe
  src="${window.location.origin}/embed/${agentId}"
  style="width: 100%; height: 520px; border: 0; border-radius: 12px;"
></iframe>`
}

function buildSnippet(agentId: string, apiKey: string, schema?: InputSchema): string {
  const body = JSON.stringify(exampleBody(schema), null, 2).split('\n').join('\n  ')
  return `fetch("${window.location.origin}/agents/${agentId}/invoke", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "${apiKey}"
  },
  body: JSON.stringify(${body})
})
  .then((res) => res.json())
  .then((result) => console.log(result))`
}

export function IntegratePanel({ agentId, inputSchema }: IntegratePanelProps) {
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState(false)
  const [copied, setCopied] = useState<'key' | 'snippet' | 'iframe' | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api
      .getApiKey(agentId)
      .then((res) => setApiKey(res.api_key))
      .catch((err) => setError(err instanceof ApiRequestError ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [agentId])

  const copy = (text: string, which: 'key' | 'snippet' | 'iframe') => {
    navigator.clipboard.writeText(text)
    setCopied(which)
    setTimeout(() => setCopied((cur) => (cur === which ? null : cur)), 1500)
  }

  const handleRegenerate = async () => {
    setRegenerating(true)
    setError(null)
    try {
      const res = await api.regenerateApiKey(agentId)
      setApiKey(res.api_key)
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : String(err))
    } finally {
      setRegenerating(false)
    }
  }

  if (loading) return <p className="text-sm text-neutral-400 p-1">Loading key…</p>
  if (error) return <p className="text-sm text-red-600 p-1">{error}</p>
  if (!apiKey) return null

  const requiredFields = inputSchema?.required ?? []

  return (
    <div className="max-w-2xl flex flex-col gap-6">
      <div>
        <h2 className="text-sm font-semibold text-neutral-900 mb-1">Call this agent from another site</h2>
        <p className="text-xs text-neutral-500 leading-relaxed">
          Every agent gets its own key. Send it in the <code className="font-mono">X-API-Key</code> header on
          every request to <code className="font-mono">POST /agents/{agentId}/invoke</code> — the same runtime
          this editor uses, just callable from outside.
        </p>
      </div>

      <div>
        <label className="block text-xs text-neutral-400 mb-1.5">API key</label>
        <div className="flex items-center gap-2">
          <code className="flex-1 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs font-mono text-neutral-800 truncate">
            {apiKey}
          </code>
          <Button size="sm" variant="ghost" onClick={() => copy(apiKey, 'key')}>
            {copied === 'key' ? 'Copied' : 'Copy'}
          </Button>
          <Button size="sm" variant="ghost" onClick={handleRegenerate} disabled={regenerating}>
            {regenerating ? 'Regenerating…' : 'Regenerate'}
          </Button>
        </div>
        <p className="text-[11px] text-neutral-400 mt-1.5">
          Regenerating invalidates the old key immediately — anywhere it's already in use stops working.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="block text-xs text-neutral-400">Chat embed (recommended)</label>
          <Button size="sm" variant="ghost" onClick={() => copy(buildIframeSnippet(agentId), 'iframe')}>
            {copied === 'iframe' ? 'Copied' : 'Copy snippet'}
          </Button>
        </div>
        <p className="text-[11px] text-neutral-500 mb-2 leading-relaxed">
          One tag — paste it anywhere on their site. It's a full chat window: visitors describe their
          situation in plain language, it asks for whatever's still missing, and it remembers the
          conversation if the page reloads.
        </p>
        <pre className="rounded-md border border-neutral-200 bg-neutral-900 text-neutral-100 text-xs font-mono p-3 overflow-x-auto mb-3">
          {buildIframeSnippet(agentId)}
        </pre>
        <iframe
          src={`${window.location.origin}/embed/${agentId}`}
          className="w-full rounded-xl border border-neutral-200"
          style={{ height: 420 }}
          title="Chat embed preview"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="block text-xs text-neutral-400">Advanced: raw API request</label>
          <Button size="sm" variant="ghost" onClick={() => copy(buildSnippet(agentId, apiKey, inputSchema), 'snippet')}>
            {copied === 'snippet' ? 'Copied' : 'Copy snippet'}
          </Button>
        </div>
        <p className="text-[11px] text-neutral-500 mb-2 leading-relaxed">
          For calling from your own backend with structured fields instead of the chat widget.
        </p>
        <pre className="rounded-md border border-neutral-200 bg-neutral-900 text-neutral-100 text-xs font-mono p-3 overflow-x-auto">
          {buildSnippet(agentId, apiKey, inputSchema)}
        </pre>
        {requiredFields.length > 0 && (
          <p className="text-[11px] text-neutral-400 mt-1.5">
            Required fields: {requiredFields.map((f) => (
              <code key={f} className="font-mono">{f}</code>
            )).reduce((acc, el, i) => (i === 0 ? [el] : [...acc, ', ', el]), [] as ReactNode[])}
          </p>
        )}
      </div>

      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5">
        <Badge tone="warning" uppercase className="mb-1.5">Demo build</Badge>
        <p className="text-xs text-amber-800 leading-relaxed">
          This key gates access per agent, but the server isn't hardened for production traffic yet —
          no rate limiting, no HTTPS enforced. Fine for a demo on a shared network; tighten before
          any real deployment.
        </p>
      </div>
    </div>
  )
}
