import type { ChatContentType } from '../types'
import { Markdown } from './Markdown'

interface ContentRendererProps {
  contentType: ChatContentType | string
  content: string
}

const MEDIA_ICON: Record<string, string> = {
  image: '🖼️',
  video: '🎬',
  audio: '🎵',
}

const POINT_ACCENTS = ['bg-brand-500', 'bg-emerald-500', 'bg-amber-500', 'bg-violet-500', 'bg-rose-500']

interface InfographicSpec {
  title: string
  points: { label: string; detail: string }[]
}

function parseInfographic(content: string): InfographicSpec | null {
  try {
    const parsed = JSON.parse(content)
    if (
      parsed &&
      typeof parsed.title === 'string' &&
      Array.isArray(parsed.points) &&
      parsed.points.length > 0 &&
      parsed.points.every((p: unknown) => {
        const point = p as { label?: unknown; detail?: unknown }
        return typeof point.label === 'string' && typeof point.detail === 'string'
      })
    ) {
      return parsed as InfographicSpec
    }
  } catch {
    // not JSON — falls through to caption rendering
  }
  return null
}

function InfographicCard({ spec }: { spec: InfographicSpec }) {
  return (
    <div className="mt-1 rounded-lg border border-neutral-200 bg-white p-4 flex flex-col gap-3 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="text-lg leading-none">🖼️</span>
        <h4 className="text-sm font-semibold text-neutral-800">{spec.title}</h4>
      </div>
      <div className="flex flex-col gap-2.5">
        {spec.points.map((point, i) => (
          <div key={i} className="flex items-start gap-2.5">
            <span
              className={`mt-1 shrink-0 w-2 h-2 rounded-full ${POINT_ACCENTS[i % POINT_ACCENTS.length]}`}
            />
            <div>
              <div className="text-xs font-medium text-neutral-700">{point.label}</div>
              <div className="text-xs text-neutral-500 leading-relaxed">{point.detail}</div>
            </div>
          </div>
        ))}
      </div>
      <span className="text-[10px] uppercase tracking-wide text-neutral-300 font-medium">
        Generated infographic
      </span>
    </div>
  )
}

/** Renders an agent's {content_type, content} output. text/code render for real.
 *  image renders as a real generated infographic when `content` is the expected
 *  structured JSON, falling back to a captioned placeholder card if it isn't (or
 *  for video/audio, where no generation model is connected at all).
 */
export function ContentRenderer({ contentType, content }: ContentRendererProps) {
  if (contentType === 'code') {
    return (
      <pre className="mt-1 rounded-md bg-neutral-900 text-neutral-100 text-xs p-3 overflow-x-auto font-mono whitespace-pre-wrap">
        {content}
      </pre>
    )
  }

  if (contentType === 'image') {
    const infographic = parseInfographic(content)
    if (infographic) return <InfographicCard spec={infographic} />
    // Fell back: content wasn't the expected structured shape — still show something useful.
  }

  if (contentType === 'image' || contentType === 'video' || contentType === 'audio') {
    return (
      <div className="mt-1 rounded-lg border border-dashed border-neutral-300 bg-neutral-50 p-4 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg leading-none">{MEDIA_ICON[contentType]}</span>
          <span className="text-[10px] uppercase tracking-wide text-neutral-400 font-medium">
            {contentType} — demo placeholder, no generation model connected
          </span>
        </div>
        <p className="text-sm text-neutral-700 leading-relaxed">{content}</p>
      </div>
    )
  }

  // Agents reply in markdown -- **bold**, bullet lists, numbered steps -- which
  // rendered as literal asterisks before this.
  return <Markdown content={content} />
}
