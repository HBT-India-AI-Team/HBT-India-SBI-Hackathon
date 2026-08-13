export type StepStatus = 'pending' | 'active' | 'done' | 'error'

export interface ProgressStep {
  id: string
  label: string
  status: StepStatus
}

// Shared by the "describe it" agent-generation modal and "Fix with AI" —
// both stream step-by-step SSE progress over calls that can take minutes
// against a slow shared Ollama host, so a plain spinner leaves the user
// guessing whether it's stuck or just on step 2 of 3.
export function ProgressChecklist({ steps }: { steps: ProgressStep[] }) {
  return (
    <ul className="mb-4 flex flex-col gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-3">
      {steps.map((step) => (
        <li key={step.id} className="flex items-center gap-2.5 text-sm">
          {step.status === 'done' ? (
            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 text-brand-600" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          ) : step.status === 'error' ? (
            <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0 text-red-600" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : step.status === 'active' ? (
            <span className="w-4 h-4 shrink-0 rounded-full border-2 border-brand-300 border-t-brand-600 animate-spin" />
          ) : (
            <span className="w-4 h-4 shrink-0 rounded-full border-2 border-neutral-300" />
          )}
          <span
            className={
              step.status === 'done'
                ? 'text-neutral-500 line-through decoration-neutral-300'
                : step.status === 'error'
                  ? 'text-red-700'
                  : step.status === 'active'
                    ? 'text-neutral-900 font-medium'
                    : 'text-neutral-400'
            }
          >
            {step.label}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function upsertStep(prev: ProgressStep[], id: string, label: string, status: StepStatus): ProgressStep[] {
  const idx = prev.findIndex((s) => s.id === id)
  if (idx === -1) return [...prev, { id, label, status }]
  const next = [...prev]
  next[idx] = { ...next[idx], label, status }
  return next
}
