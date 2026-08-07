import { useEffect, useRef, useState } from 'react'
import { api, ApiRequestError } from '../api'
import { Badge } from './ui'
import type { BadgeTone } from './ui'
import type { ChatDecision } from '../types'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  decision?: ChatDecision | null
}

interface ChatWindowProps {
  agentId: string
}

const OUTCOME_TONE: Record<string, BadgeTone> = {
  QUALIFIED: 'success',
  CONDITIONALLY_QUALIFIED: 'brand',
  NEEDS_HUMAN_REVIEW: 'warning',
  NOT_QUALIFIED: 'danger',
}

function DecisionCard({ decision }: { decision: ChatDecision }) {
  return (
    <div className="mt-2 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Badge tone={OUTCOME_TONE[decision.outcome] ?? 'neutral'} uppercase mono>
          {decision.outcome}
        </Badge>
        {decision.composite_score !== null && (
          <span className="text-[11px] text-neutral-500 font-mono">score {decision.composite_score}</span>
        )}
      </div>
      <p className="text-xs text-neutral-600 leading-relaxed">{decision.reason}</p>
    </div>
  )
}

export function ChatWindow({ agentId }: ChatWindowProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const threadRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMessages([])
    setSessionId(null)
    setInput('')
    setError(null)
  }, [agentId])

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight })
  }, [messages, sending])

  const send = async () => {
    const message = input.trim()
    if (!message || sending) return
    setInput('')
    setError(null)
    setMessages((prev) => [...prev, { role: 'user', content: message }])
    setSending(true)
    try {
      const res = await api.chatWithAgent(agentId, sessionId, message)
      setSessionId(res.session_id)
      setMessages((prev) => [...prev, { role: 'assistant', content: res.reply, decision: res.decision }])
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : String(err))
      setMessages((prev) => prev.slice(0, -1))
      setInput(message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col h-full border border-neutral-200 rounded-lg bg-white overflow-hidden">
      <div ref={threadRef} className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.length === 0 && (
          <p className="text-sm text-neutral-400 m-auto text-center max-w-xs">
            Describe the situation in plain language — required details will be asked for as needed.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap ${
              m.role === 'user'
                ? 'self-end bg-brand-600 text-white rounded-br-sm'
                : 'self-start bg-neutral-100 text-neutral-800 rounded-bl-sm'
            }`}
          >
            {m.content}
            {m.decision && <DecisionCard decision={m.decision} />}
          </div>
        ))}
        {sending && (
          <div className="self-start text-xs text-neutral-400 italic px-1">Thinking…</div>
        )}
      </div>

      {error && <p className="text-xs text-red-600 px-4 pb-1">{error}</p>}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
        className="flex items-center gap-2 border-t border-neutral-200 p-3 shrink-0"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message…"
          disabled={sending}
          className="flex-1 rounded-md border border-neutral-300 bg-white text-sm px-3 py-2 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="shrink-0 rounded-md bg-brand-600 text-white text-sm font-medium px-4 py-2 transition-colors hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
        >
          Send
        </button>
      </form>
    </div>
  )
}
