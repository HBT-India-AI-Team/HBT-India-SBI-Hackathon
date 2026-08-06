import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react'

// Shared visual vocabulary for the editor UI. Every hand-styled button, form
// control, choice card, segmented toggle, badge, and modal shell used to be
// duplicated per-file with copy-pasted Tailwind strings — small drifts crept
// in (padding, disabled opacity, cursor) each time one was touched. These are
// the canonical versions; prefer them over one-off className strings.

export const fieldClasses =
  'w-full rounded-md border border-neutral-300 bg-white text-neutral-900 text-sm px-3 py-2 outline-none ' +
  'transition-colors placeholder:text-neutral-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20'

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className, ...rest } = props
  return <input className={className ? `${fieldClasses} ${className}` : fieldClasses} {...rest} />
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className, ...rest } = props
  return <textarea className={className ? `${fieldClasses} ${className}` : fieldClasses} {...rest} />
}

export function FieldLabel({ children }: { children: ReactNode }) {
  return <label className="block text-xs font-medium text-neutral-600 mb-1">{children}</label>
}

type ButtonVariant = 'primary' | 'success' | 'danger' | 'secondary' | 'ghost'
type ButtonSize = 'md' | 'sm'

const buttonVariantClasses: Record<ButtonVariant, string> = {
  primary: 'bg-brand-600 hover:bg-brand-700 text-white shadow-sm shadow-brand-900/10',
  success: 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm shadow-emerald-900/10',
  danger: 'bg-red-600 hover:bg-red-700 text-white shadow-sm shadow-red-900/10',
  secondary: 'bg-neutral-100 hover:bg-neutral-200 text-neutral-800 border border-neutral-200',
  ghost: 'text-neutral-600 hover:bg-neutral-100',
}

const buttonSizeClasses: Record<ButtonSize, string> = {
  md: 'px-3.5 py-2 text-sm',
  sm: 'px-3 py-1.5 text-sm',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
}

export function Button({ variant = 'primary', size = 'md', className, children, ...rest }: ButtonProps) {
  const base =
    'inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors cursor-pointer ' +
    'disabled:opacity-50 disabled:cursor-not-allowed'
  return (
    <button
      className={[base, buttonVariantClasses[variant], buttonSizeClasses[size], className].filter(Boolean).join(' ')}
      {...rest}
    >
      {children}
    </button>
  )
}

interface ChoiceCardProps {
  selected: boolean
  onClick: () => void
  title: ReactNode
  subtitle?: ReactNode
  disabled?: boolean
  mono?: boolean
}

export function ChoiceCard({ selected, onClick, title, subtitle, disabled, mono }: ChoiceCardProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-pressed={selected}
      className={`text-left rounded-lg border px-3 py-2.5 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${
        selected ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-500/30' : 'border-neutral-200 hover:border-neutral-300 hover:bg-neutral-50'
      }`}
    >
      <div
        className={`text-sm font-medium truncate ${mono ? 'font-mono text-xs' : ''} ${
          selected ? 'text-brand-700' : 'text-neutral-800'
        }`}
      >
        {title}
      </div>
      {subtitle && <div className="text-[11px] text-neutral-500 mt-0.5 line-clamp-2">{subtitle}</div>}
    </button>
  )
}

interface SegmentedControlProps<T extends string> {
  options: { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
}

export function SegmentedControl<T extends string>({ options, value, onChange }: SegmentedControlProps<T>) {
  return (
    <div className="flex gap-0.5 bg-neutral-100 border border-neutral-200 rounded-md p-0.5">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          aria-pressed={value === opt.value}
          className={`text-xs capitalize px-2.5 py-1 rounded transition-colors cursor-pointer ${
            value === opt.value ? 'bg-brand-600 text-white' : 'text-neutral-500 hover:text-neutral-800'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

interface ModalProps {
  onClose: () => void
  maxWidth?: 'sm' | 'lg'
  children: ReactNode
}

export function Modal({ onClose, maxWidth = 'lg', children }: ModalProps) {
  return (
    <div
      className="fixed inset-0 bg-neutral-900/40 backdrop-blur-[2px] flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className={`bg-white border border-neutral-200 rounded-lg w-full ${
          maxWidth === 'sm' ? 'max-w-sm' : 'max-w-lg'
        } p-5 shadow-2xl shadow-neutral-900/20 max-h-[90vh] overflow-y-auto`}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}

export function TrashIcon({ className = 'w-3.5 h-3.5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6h16Z" />
    </svg>
  )
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
}

export function DangerIconButton({ label, className, children, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={`text-neutral-400 hover:text-red-600 transition-colors p-1 -m-1 rounded cursor-pointer ${className ?? ''}`}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-neutral-200 ${className ?? ''}`} />
}

type BadgeTone = 'brand' | 'gold' | 'success' | 'warning' | 'danger' | 'neutral'

const badgeToneClasses: Record<BadgeTone, string> = {
  brand: 'text-brand-700 bg-brand-50 border-brand-200',
  gold: 'text-gold-800 bg-gold-50 border-gold-200',
  success: 'text-emerald-700 bg-emerald-50 border-emerald-200',
  warning: 'text-amber-700 bg-amber-50 border-amber-200',
  danger: 'text-red-700 bg-red-50 border-red-200',
  neutral: 'text-neutral-600 bg-neutral-100 border-neutral-200',
}

interface BadgeProps {
  tone?: BadgeTone
  mono?: boolean
  uppercase?: boolean
  children: ReactNode
  className?: string
}

export function Badge({ tone = 'neutral', mono, uppercase, children, className }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 shrink-0 text-[11px] rounded px-1.5 py-0.5 border ${
        mono ? 'font-mono' : 'font-medium'
      } ${uppercase ? 'uppercase tracking-wide' : ''} ${badgeToneClasses[tone]} ${className ?? ''}`}
    >
      {children}
    </span>
  )
}

export function StatusDot({ tone = 'success' }: { tone?: 'success' | 'danger' }) {
  return <span className={`w-2 h-2 rounded-full shrink-0 ${tone === 'success' ? 'bg-emerald-500' : 'bg-red-500'}`} />
}
