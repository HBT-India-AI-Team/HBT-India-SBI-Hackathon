import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, ApiRequestError } from '../api'
import { Badge } from './ui'
import type { BadgeTone } from './ui'
import { ContentRenderer } from './ContentRenderer'
import type { ChatDecision, ChatContentType, StageTraceEntry } from '../types'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  decision?: ChatDecision | null
  contentType?: ChatContentType | null
  stageTrace?: StageTraceEntry[] | null
}

interface ChatWindowProps {
  agentId: string
  /** {"message": "..."} — prefills the input box via "Try sample message" when present. */
  demoSampleInput?: Record<string, unknown> | null
}

/** Roughly six lines. Past this the composer stops growing and scrolls, so a
 *  long paste can't push the thread off screen. */
const MAX_INPUT_HEIGHT = 160

/** Abort reason marking a teardown rather than the user pressing stop. */
const AGENT_SWITCHED = 'agent-switched'

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

/** A composer mode toggle.
 *
 *  Both modes are sent per message rather than held on the session, so the
 *  same question can be asked twice in one thread and the answers read side
 *  by side — which is the only way to judge either of them. */
function ModePill({ label, on, onToggle, title }: {
  label: string
  on: boolean
  onToggle: () => void
  title: string
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={on}
      title={title}
      className={`shrink-0 flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium cursor-pointer transition-colors ${
        on
          ? 'border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100'
          : 'border-neutral-200 bg-white text-neutral-400 hover:text-neutral-600'
      }`}
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${on ? 'bg-brand-500' : 'bg-neutral-300'}`}
      />
      {label}
    </button>
  )
}

/** Says whether the vernacular layer actually reached this answer.
 *
 *  It has several ways to produce nothing — switched off, a script with no
 *  corpus, or a question no passage scored close enough to — and from the
 *  outside they are indistinguishable from each other and from a broken
 *  feature. Toggling the switch and seeing the reply not change is the
 *  expected outcome for most English questions; without this line that reads
 *  as a bug, and it is the reading a demo audience will reach for first. */
function StyleBadge({ trace }: { trace: StageTraceEntry[] }) {
  const style = trace.find((t) => t.detail?.style)?.detail?.style
  const spoken = trace.some((t) => t.detail?.voice)
  if (!style && !spoken) return null

  const sources = [
    style?.guide ? `${style.language ?? ''} register guide`.trim() : null,
    style?.examples ? `${style.examples} example${style.examples === 1 ? '' : 's'}` : null,
  ].filter(Boolean)

  const parts = [
    !style ? null : style.applied
      ? `colloquial style: ${sources.join(' + ')}`
      : `plain style — ${style.reason ?? 'not applied'}`,
    spoken ? 'spoken answer (short, no markdown)' : null,
  ].filter(Boolean)

  return (
    <div className="mt-1.5 text-[11px] text-neutral-400 flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full shrink-0 ${
          style?.applied || spoken ? 'bg-brand-400' : 'bg-neutral-300'
        }`}
      />
      {parts.join(' · ')}
    </div>
  )
}

function ThinkingTrace({ trace }: { trace: StageTraceEntry[] }) {
  const [open, setOpen] = useState(false)
  const totalMs = trace.reduce((sum, t) => sum + t.duration_ms, 0)
  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-[11px] text-neutral-400 hover:text-neutral-600 transition-colors cursor-pointer flex items-center gap-1"
      >
        <span className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
        Thinking ({trace.length} steps, {Math.round(totalMs)}ms)
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-0.5 border-l-2 border-neutral-200 pl-2.5">
          {trace.map((t, i) => (
            <li key={i} className="text-[11px] font-mono text-neutral-500">
              <div className="flex items-center gap-1.5">
                <span className={t.status === 'error' ? 'text-red-500' : 'text-neutral-400'}>
                  {t.status === 'ok' ? '✓' : t.status === 'error' ? '✕' : '·'}
                </span>
                <span>{t.stage}</span>
                <span className="text-neutral-300">— {t.summary}</span>
                <span className="text-neutral-300 ml-auto">{Math.round(t.duration_ms)}ms</span>
              </div>
              {/* Tool calls and the model's own reasoning, when this stage
                  produced them — same real data the Playground's AI
                  Observation column shows. */}
              {t.detail?.tool_calls?.map((call, j) => (
                <div key={j} className="ml-4 mt-0.5 text-[10px] text-brand-700 break-all">
                  ↳ {call.name}({JSON.stringify(call.arguments)})
                </div>
              ))}
              {t.detail?.thinking && (
                <details className="ml-4 mt-0.5">
                  <summary className="text-[10px] text-brand-600 cursor-pointer">
                    model's reasoning ({t.detail.thinking.length.toLocaleString()} chars)
                  </summary>
                  <pre className="mt-1 text-[10px] leading-relaxed text-neutral-600 bg-white border border-neutral-200 rounded p-2 max-h-56 overflow-y-auto whitespace-pre-wrap">
                    {t.detail.thinking}
                  </pre>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function ChatWindow({ agentId, demoSampleInput }: ChatWindowProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  // Deliberately survive an agent switch, unlike the thread below them: these
  // are viewing preferences, and having one silently snap back is the more
  // surprising behaviour. Defaults mirror the backend's — style on, voice off.
  const [styleOn, setStyleOn] = useState(true)
  const [voiceOn, setVoiceOn] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const threadRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const sampleMessage = typeof demoSampleInput?.message === 'string' ? demoSampleInput.message : null

  useEffect(() => {
    // Switching agents mid-flight would otherwise land the previous agent's
    // reply in the new agent's thread. Tagged, because the rejection this
    // causes lands after the reset below and must not restore the old
    // agent's text into the new agent's composer.
    abortRef.current?.abort(AGENT_SWITCHED)
    setMessages([])
    setSessionId(null)
    setInput('')
    setError(null)
  }, [agentId])

  useEffect(() => () => abortRef.current?.abort(), [])

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight })
  }, [messages, sending])

  // Grow the composer with its content, then scroll inside it once it hits the
  // cap. Height has to be cleared to 'auto' first: scrollHeight never reports
  // less than the element's current height, so without the reset the box can
  // grow but never shrink back after a delete.
  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return
    // scrollHeight excludes the border, but box-sizing here is border-box, so
    // assigning it straight across leaves the box a border's worth too short
    // and clips descenders. Add it back.
    const style = window.getComputedStyle(el)
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)
    el.style.height = 'auto'
    const wanted = el.scrollHeight + border
    el.style.height = `${Math.min(wanted, MAX_INPUT_HEIGHT)}px`
    el.style.overflowY = wanted > MAX_INPUT_HEIGHT ? 'auto' : 'hidden'
  }, [input])

  const stopSending = () => abortRef.current?.abort()

  const sendMessage = async (message: string) => {
    if (!message || sending) return
    setInput('')
    setError(null)
    setMessages((prev) => [...prev, { role: 'user', content: message }])
    setSending(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const res = await api.chatWithAgent(agentId, sessionId, message, {
        style: styleOn,
        voice: voiceOn,
        signal: controller.signal,
      })
      setSessionId(res.session_id)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: res.reply,
          decision: res.decision,
          contentType: res.content_type,
          stageTrace: res.stage_trace,
        },
      ])
    } catch (err) {
      if (controller.signal.reason === AGENT_SWITCHED) return
      // Both paths put the message back in the composer so it can be edited
      // and resent; stopping just isn't a failure, so it shows no red error.
      setMessages((prev) => prev.slice(0, -1))
      setInput(message)
      if (!controller.signal.aborted) {
        setError(err instanceof ApiRequestError ? err.message : String(err))
      }
    } finally {
      abortRef.current = null
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col h-full border border-neutral-200 rounded-lg bg-white overflow-hidden">
      <div ref={threadRef} className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.length === 0 && (
          <div className="m-auto flex flex-col items-center gap-3 max-w-xs text-center">
            <p className="text-sm text-neutral-400">
              Describe the situation in plain language — required details will be asked for as needed.
            </p>
            {sampleMessage && (
              <button
                type="button"
                onClick={() => setInput(sampleMessage)}
                className="text-xs font-medium text-brand-600 hover:text-brand-700 border border-brand-200 bg-brand-50 rounded-md px-3 py-1.5 cursor-pointer transition-colors"
              >
                Try sample message
              </button>
            )}
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed ${
              m.role === 'user'
                ? 'self-end bg-brand-600 text-white rounded-br-sm whitespace-pre-wrap'
                : 'self-start bg-neutral-100 text-neutral-800 rounded-bl-sm'
            }`}
          >
            {/* Keyed off role, not contentType: an agent reply that omits
                content_type is still markdown and still needs rendering,
                while the user's own text stays literal. */}
            {m.role === 'assistant' ? (
              <ContentRenderer contentType={m.contentType ?? 'text'} content={m.content} />
            ) : (
              <span className="whitespace-pre-wrap">{m.content}</span>
            )}
            {m.decision && <DecisionCard decision={m.decision} />}
            {m.stageTrace && m.stageTrace.length > 0 && (
              <>
                <StyleBadge trace={m.stageTrace} />
                <ThinkingTrace trace={m.stageTrace} />
              </>
            )}
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
          sendMessage(input.trim())
        }}
        className="border-t border-neutral-200 p-3 shrink-0"
      >
        {/* The border, padding and focus ring live on this wrapper rather than
            on the textarea, so the send button sits inside the same box the
            user is typing in and the whole thing grows as one unit.

            Stacked, not side-by-side: the textarea spans the full width and
            the button gets its own row beneath it. Sitting the button beside
            the text left it stranded against the scrollbar once the box grew,
            and cost the text a button's width on every line. */}
        <div className="rounded-2xl border border-neutral-300 bg-white px-3 pt-2.5 pb-2 shadow-sm transition-colors focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20">
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter writes a newline. isComposing is what
              // keeps this usable in Hindi and Tamil: IME candidates are
              // committed with Enter, and without the guard that keystroke
              // would fire off a half-typed word instead of finishing it.
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                sendMessage(input.trim())
              }
            }}
            placeholder="Type a message…"
            disabled={sending}
            className="block w-full resize-none border-0 bg-transparent text-sm leading-relaxed outline-none placeholder:text-neutral-400 disabled:opacity-60"
          />
          <div className="mt-1.5 flex items-center gap-2">
            <div className="mr-auto flex items-center gap-2 min-w-0">
              <ModePill
                label="Colloquial"
                on={styleOn}
                onToggle={() => setStyleOn((s) => !s)}
                title={
                  styleOn
                    ? 'Colloquial style is on — Hindi and Tamil answers are written the way people actually speak. Turn it off to compare against the plain answer.'
                    : 'Colloquial style is off — answers use the model’s default register.'
                }
              />
              <ModePill
                label="Voice"
                on={voiceOn}
                onToggle={() => setVoiceOn((v) => !v)}
                title={
                  voiceOn
                    ? 'Voice mode is on — answers come back as two to four spoken sentences with no markdown, the way the voice client will receive them.'
                    : 'Voice mode is off — answers are written for a screen.'
                }
              />
              {/* Shift+Enter is invisible otherwise, and the hint doubles as the
                  thing that keeps the buttons from reading as orphans in an
                  otherwise empty row. Hidden earlier now that two pills share
                  the space with it. */}
              <span className="hidden xl:block truncate text-[11px] text-neutral-400 select-none">
                <span className="font-medium text-neutral-500">Enter</span> to send ·{' '}
                <span className="font-medium text-neutral-500">Shift+Enter</span> for a new line
              </span>
            </div>
            {/* Icon button rather than a "Send" label: the composer grows to
                six lines, and a full-width pill reads as a second panel
                instead of an action. */}
            <button
              type={sending ? 'button' : 'submit'}
              onClick={sending ? stopSending : undefined}
              disabled={!sending && !input.trim()}
              aria-label={sending ? 'Stop' : 'Send message'}
              title={sending ? 'Stop waiting for this reply' : 'Send  ·  Shift+Enter for a new line'}
              className={`shrink-0 grid place-items-center h-8 w-8 rounded-full text-white transition-colors disabled:bg-neutral-200 disabled:text-neutral-400 disabled:cursor-not-allowed cursor-pointer ${
                sending ? 'bg-neutral-700 hover:bg-neutral-800' : 'bg-brand-600 hover:bg-brand-700'
              }`}
            >
              {sending ? (
                // Replies run 6-26s. The ring keeps the wait on the control the
                // user just pressed; the square inside it is what they click to
                // get out of it.
                <span className="relative grid place-items-center">
                  <svg className="absolute h-7 w-7 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" className="opacity-25" />
                    <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  <svg className="h-3 w-3" viewBox="0 0 24 24" aria-hidden="true">
                    <rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" />
                  </svg>
                </span>
              ) : (
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M12 19V5M12 5l-6 6M12 5l6 6"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}
