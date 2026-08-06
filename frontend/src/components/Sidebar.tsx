import type { ComponentType } from 'react'

type SidebarView = 'agents' | 'playground'

interface SidebarProps {
  active: SidebarView
  onNavigate: (view: SidebarView) => void
}

function MarkIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-4.5 h-4.5 text-white" fill="none" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z" />
    </svg>
  )
}

function AgentsIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth="1.75">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  )
}

function PlaygroundIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-[18px] h-[18px]" fill="none" stroke="currentColor" strokeWidth="1.75">
      <path strokeLinecap="round" strokeLinejoin="round" d="M8 5.14v13.72a1 1 0 0 0 1.5.86l11-6.86a1 1 0 0 0 0-1.72l-11-6.86A1 1 0 0 0 8 5.14Z" />
    </svg>
  )
}

const navItems: { key: SidebarView; label: string; icon: ComponentType }[] = [
  { key: 'agents', label: 'Agents', icon: AgentsIcon },
  { key: 'playground', label: 'Playground', icon: PlaygroundIcon },
]

export function Sidebar({ active, onNavigate }: SidebarProps) {
  return (
    <nav className="w-56 shrink-0 h-full bg-brand-900 flex flex-col py-5">
      <div className="px-5 mb-6 flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-md bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shrink-0 ring-1 ring-white/10">
          <MarkIcon />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-white truncate">Agent Editor</div>
          <div className="text-[11px] text-brand-300 truncate">Agentic AI Platform</div>
        </div>
      </div>

      <div className="flex-1 px-3 flex flex-col gap-0.5">
        {navItems.map(({ key, label, icon: Icon }) => {
          const selected = active === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => onNavigate(key)}
              aria-current={selected ? 'page' : undefined}
              className={`relative flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer ${
                selected ? 'bg-white/10 text-white' : 'text-brand-200 hover:bg-white/5 hover:text-white'
              }`}
            >
              {selected && <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-gold-400" />}
              <Icon />
              {label}
            </button>
          )
        })}
      </div>
    </nav>
  )
}
