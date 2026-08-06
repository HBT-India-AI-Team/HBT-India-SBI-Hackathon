import { useMemo } from 'react'
import { load } from 'js-yaml'

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function ValueView({ value, depth }: { value: unknown; depth: number }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-neutral-400 italic text-sm">—</span>
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-neutral-400 italic text-sm">empty</span>

    const allPrimitive = value.every((item) => !isPlainObject(item) && !Array.isArray(item))
    if (allPrimitive) {
      return (
        <div className="flex flex-wrap gap-1.5">
          {value.map((item, i) => (
            <span
              key={i}
              className="text-xs font-mono bg-brand-50 border border-brand-200 text-brand-700 rounded px-1.5 py-0.5"
            >
              {String(item)}
            </span>
          ))}
        </div>
      )
    }

    return (
      <div className="flex flex-col gap-2">
        {value.map((item, i) => (
          <div key={i} className="border-l-2 border-neutral-200 pl-3">
            <ValueView value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    )
  }

  if (isPlainObject(value)) {
    return <ObjectView obj={value} depth={depth} />
  }

  if (typeof value === 'string' && value.includes('\n')) {
    return <p className="text-sm text-neutral-700 leading-relaxed whitespace-pre-wrap">{value.trim()}</p>
  }

  return <span className="text-sm text-neutral-800 font-mono">{String(value)}</span>
}

function ObjectView({ obj, depth }: { obj: Record<string, unknown>; depth: number }) {
  const entries = Object.entries(obj)
  if (entries.length === 0) return <span className="text-neutral-400 italic text-sm">empty</span>

  return (
    <div className={`flex flex-col ${depth === 0 ? 'gap-4' : 'gap-2.5'}`}>
      {entries.map(([key, val]) => (
        <div key={key}>
          <div className="text-[11px] font-mono uppercase tracking-wide text-neutral-500 mb-1">{key}</div>
          <ValueView value={val} depth={depth + 1} />
        </div>
      ))}
    </div>
  )
}

interface YamlPreviewProps {
  value: string
}

export function YamlPreview({ value }: YamlPreviewProps) {
  const parsed = useMemo(() => {
    try {
      return { ok: true as const, data: load(value) }
    } catch (err) {
      return { ok: false as const, error: err instanceof Error ? err.message : String(err) }
    }
  }, [value])

  if (!parsed.ok) {
    return (
      <div className="h-full flex items-center justify-center p-6 text-center">
        <div>
          <p className="text-sm text-neutral-500">Can't preview — this isn't valid YAML right now.</p>
          <p className="text-xs text-neutral-400 mt-1.5 font-mono">{parsed.error}</p>
        </div>
      </div>
    )
  }

  if (!isPlainObject(parsed.data)) {
    return (
      <div className="h-full flex items-center justify-center p-6 text-center text-sm text-neutral-500">
        Nothing to preview.
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto p-4">
      <ObjectView obj={parsed.data} depth={0} />
    </div>
  )
}
