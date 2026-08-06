import type { Capability } from '../types'

interface ReferencePanelProps {
  stages: string[]
  capabilities: Capability[]
}

export function ReferencePanel({ stages, capabilities }: ReferencePanelProps) {
  return (
    <div className="flex flex-col gap-6 overflow-y-auto h-full text-sm">
      <p className="text-neutral-500 text-xs leading-relaxed">
        These are the building blocks already registered in the runtime. An agent whose pipeline
        only needs stages from this list — plus new rules/instructions — can be built entirely
        through this editor, no code changes. A genuinely new kind of lookup or algorithm needs a
        developer to add one new stage.
      </p>

      <div>
        <h3 className="text-neutral-700 font-medium mb-2">Available pipeline stages</h3>
        <div className="flex flex-wrap gap-2">
          {stages.map((stage) => (
            <span
              key={stage}
              className="font-mono text-xs bg-brand-50 border border-brand-200 text-brand-700 rounded-md px-2 py-1"
            >
              {stage}
            </span>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-neutral-700 font-medium mb-2">Available capabilities</h3>
        <div className="flex flex-col gap-2">
          {capabilities.map((cap) => (
            <div
              key={cap.name}
              className="bg-white border border-neutral-200 rounded-lg px-3 py-2 transition-colors hover:border-neutral-300"
            >
              <div className="font-mono text-xs text-emerald-700">{cap.name}</div>
              <div className="text-xs text-neutral-500 mt-0.5">{cap.description}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
