import type { ReactNode } from 'react'

/** Minimal markdown renderer for agent replies.
 *
 *  Deliberately not react-markdown: agents emit a narrow, predictable subset
 *  (bold, bullets, numbered steps, the occasional heading and source link),
 *  and a dependency-free renderer keeps the demo build from needing a network
 *  install. It also renders into React nodes rather than an HTML string, so
 *  there is no dangerouslySetInnerHTML anywhere and model output cannot inject
 *  markup no matter what comes back.
 *
 *  Anything it doesn't recognise falls through as literal text, which is the
 *  behaviour this replaced -- so an unsupported construct degrades to what the
 *  user saw before rather than disappearing.
 */

/** `[label](href)` from a model is untrusted: only http(s) and mail links
 *  become anchors, so a `javascript:` URL renders as plain text instead. */
function safeHref(href: string): string | null {
  const url = href.trim()
  return /^(https?:\/\/|mailto:)/i.test(url) ? url : null
}

// No lookbehind/lookahead on purpose: a regex the browser's engine rejects is
// a syntax error at module load, which blanks the page rather than degrading.
// `**bold**` is listed before `*italic*`, and alternation is ordered, so the
// bold form always wins at a given position.
const INLINE_RE = /(\*\*[^\n]+?\*\*|__[^\n]+?__|\*[^\s*][^*\n]*?\*|`[^`\n]+?`|\[[^\]\n]+?\]\([^\s)]+?\))/g

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let i = 0
  let match: RegExpExecArray | null
  INLINE_RE.lastIndex = 0

  while ((match = INLINE_RE.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const token = match[0]
    const key = `${keyPrefix}-i${i++}`

    if (token.startsWith('**') || token.startsWith('__')) {
      nodes.push(
        <strong key={key} className="font-semibold text-neutral-900">
          {token.slice(2, -2)}
        </strong>,
      )
    } else if (token.startsWith('`')) {
      nodes.push(
        <code key={key} className="px-1 py-0.5 rounded bg-neutral-100 text-[0.9em] font-mono">
          {token.slice(1, -1)}
        </code>,
      )
    } else if (token.startsWith('[')) {
      const link = /^\[(.+?)\]\((.+?)\)$/.exec(token)
      const href = link ? safeHref(link[2]) : null
      if (link && href) {
        nodes.push(
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-600 underline underline-offset-2 hover:text-brand-700 break-all"
          >
            {link[1]}
          </a>,
        )
      } else {
        nodes.push(token)
      }
    } else {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>)
    }
    last = match.index + token.length
  }

  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

type Block =
  | { type: 'p'; text: string }
  | { type: 'h'; level: number; text: string }
  | { type: 'ul'; items: string[] }
  // Carries the number the model actually wrote. A list whose items have
  // sub-bullets between them parses as several ol blocks, so counting per
  // block would restart "2." back at "1." -- which is what a real reply
  // (PMJJBY / PMSBY, each with its own cost and cover bullets) does.
  | { type: 'ol'; items: { num: number; text: string }[] }

const UL_RE = /^\s*[-*+]\s+/
const OL_RE = /^\s*\d+[.)]\s+/
const H_RE = /^(#{1,6})\s+(.*)$/
/** A line that starts a new block, so a paragraph knows where to stop. */
const BLOCK_START_RE = /^\s*([-*+]\s|\d+[.)]\s|#{1,6}\s)/

function parseBlocks(markdown: string): Block[] {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    if (!line.trim()) {
      i++
      continue
    }

    const heading = H_RE.exec(line)
    if (heading) {
      blocks.push({ type: 'h', level: heading[1].length, text: heading[2] })
      i++
      continue
    }

    if (UL_RE.test(line)) {
      const items: string[] = []
      while (i < lines.length && UL_RE.test(lines[i])) {
        items.push(lines[i].replace(UL_RE, ''))
        i++
      }
      blocks.push({ type: 'ul', items })
      continue
    }

    if (OL_RE.test(line)) {
      const items: { num: number; text: string }[] = []
      while (i < lines.length && OL_RE.test(lines[i])) {
        const marker = /^\s*(\d+)[.)]\s+/.exec(lines[i])
        items.push({
          num: marker ? parseInt(marker[1], 10) : items.length + 1,
          text: lines[i].replace(OL_RE, ''),
        })
        i++
      }
      blocks.push({ type: 'ol', items })
      continue
    }

    // Soft-wrapped paragraph: keep consuming until a blank line or a line that
    // opens a different block.
    const paragraph: string[] = []
    while (i < lines.length && lines[i].trim() && !BLOCK_START_RE.test(lines[i])) {
      paragraph.push(lines[i])
      i++
    }
    blocks.push({ type: 'p', text: paragraph.join('\n') })
  }

  return blocks
}

const HEADING_SIZE: Record<number, string> = {
  1: 'text-base font-semibold',
  2: 'text-sm font-semibold',
  3: 'text-sm font-semibold',
  4: 'text-sm font-medium',
  5: 'text-sm font-medium',
  6: 'text-sm font-medium',
}

export function Markdown({ content }: { content: string }) {
  const blocks = parseBlocks(content)

  return (
    <div className="flex flex-col gap-2 leading-relaxed">
      {blocks.map((block, b) => {
        const key = `b${b}`
        if (block.type === 'h') {
          return (
            <div key={key} className={`${HEADING_SIZE[block.level]} text-neutral-900 mt-1`}>
              {renderInline(block.text, key)}
            </div>
          )
        }
        if (block.type === 'ul') {
          return (
            <ul key={key} className="flex flex-col gap-1 pl-1">
              {block.items.map((item, j) => (
                <li key={`${key}-${j}`} className="flex gap-2">
                  <span className="text-neutral-400 select-none shrink-0">•</span>
                  <span>{renderInline(item, `${key}-${j}`)}</span>
                </li>
              ))}
            </ul>
          )
        }
        if (block.type === 'ol') {
          return (
            <ol key={key} className="flex flex-col gap-1 pl-1">
              {block.items.map((item, j) => (
                <li key={`${key}-${j}`} className="flex gap-2">
                  <span className="text-neutral-400 select-none shrink-0 tabular-nums">{item.num}.</span>
                  <span>{renderInline(item.text, `${key}-${j}`)}</span>
                </li>
              ))}
            </ol>
          )
        }
        return (
          <p key={key} className="whitespace-pre-wrap">
            {renderInline(block.text, key)}
          </p>
        )
      })}
    </div>
  )
}
