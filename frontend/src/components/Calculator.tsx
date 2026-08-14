import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ToolSuggestion } from '../types'

/** An inline calculator, rendered from its definition rather than hard-coded.
 *
 *  Nothing is computed here. Every result comes from POSTing the inputs to
 *  the backend, which runs the same registered capability the agent called
 *  when it answered in prose. Doing the arithmetic in the browser would mean
 *  two implementations of an EMI formula, and the first time they disagreed
 *  the user would be looking at a contradiction with no way to tell which
 *  number was real.
 *
 *  It follows that a calculator can be slow, and that it can fail. Both are
 *  better than being confidently wrong. */
export function Calculator({ suggestion, userId }: {
  suggestion: ToolSuggestion
  userId?: string | null
}) {
  const { tool, prefill, reason } = suggestion
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      tool.inputs.map((field) => [field.key, prefill[field.key] != null ? String(prefill[field.key]) : '']),
    ),
  )
  const [result, setResult] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const complete = tool.inputs.every((field) => values[field.key]?.trim())

  const compute = useCallback(async (current: Record<string, string>) => {
    if (!tool.inputs.every((field) => current[field.key]?.trim())) {
      setResult(null)
      setError(null)
      return
    }
    setBusy(true)
    try {
      const res = await api.executeTool(tool.tool_id, current)
      setResult(res.value)
      setError(null)
    } catch (err) {
      // A rejected input (negative tenure, zero months) comes back as a 400
      // with the reason. Showing it beats showing a stale number.
      setResult(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }, [tool])

  // Compute on open when the agent already supplied every number, so a
  // prefilled calculator shows its answer without a click.
  useEffect(() => {
    if (reason === 'computed') void compute(values)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const update = (key: string, value: string) => {
    const next = { ...values, [key]: value }
    setValues(next)
    setSaved(false)
    // Debounced: every keystroke is a round trip otherwise, and the capability
    // is doing real work on the other end.
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void compute(next), 350)
  }

  const save = async () => {
    if (!userId) return
    try {
      await api.saveTool(userId, tool.tool_id, values, result === null ? null : { value: result })
      setSaved(true)
    } catch {
      setSaved(false)
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-brand-200 bg-brand-50/40 p-3">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-semibold text-brand-800">{tool.name}</h4>
        {reason === 'computed' && (
          <span className="text-[10px] uppercase tracking-wide text-brand-600">
            from your question
          </span>
        )}
      </div>

      <div className="flex flex-col gap-2">
        {tool.inputs.map((field) => (
          <label key={field.key} className="flex items-center gap-2 text-[11px] text-neutral-600">
            <span className="w-28 shrink-0">{field.label}</span>
            <span className="flex items-center gap-1 flex-1 min-w-0">
              {field.prefix && <span className="text-neutral-400">{field.prefix}</span>}
              <input
                type="number"
                inputMode="decimal"
                value={values[field.key] ?? ''}
                min={field.min}
                step={field.step}
                onChange={(e) => update(field.key, e.target.value)}
                className="w-full min-w-0 rounded border border-neutral-300 bg-white px-2 py-1 text-xs outline-none focus:border-brand-500"
              />
              {field.suffix && <span className="text-neutral-400 whitespace-nowrap">{field.suffix}</span>}
            </span>
          </label>
        ))}
      </div>

      <div className="mt-2.5 flex items-center justify-between border-t border-brand-200 pt-2">
        <div className="text-[11px] text-neutral-500">
          {tool.output_label}
          {': '}
          {error ? (
            <span className="text-red-600">{error}</span>
          ) : result !== null ? (
            <span className="font-semibold text-neutral-900 tabular-nums">
              {tool.output_prefix ?? ''}
              {/* en-IN gives lakh/crore grouping — ₹1,06,398.02, not ₹106,398.02 */}
              {result.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
            </span>
          ) : (
            <span className="text-neutral-400">{busy ? 'calculating…' : complete ? '—' : 'fill in the fields'}</span>
          )}
        </div>
        {userId && (
          <button
            type="button"
            onClick={save}
            disabled={!complete || saved}
            className="text-[11px] text-brand-600 hover:text-brand-700 disabled:text-neutral-300 disabled:cursor-not-allowed cursor-pointer"
          >
            {saved ? 'saved' : 'save'}
          </button>
        )}
      </div>
    </div>
  )
}
