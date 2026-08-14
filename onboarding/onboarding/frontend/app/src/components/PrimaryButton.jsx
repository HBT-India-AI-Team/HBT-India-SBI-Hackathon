export function PrimaryButton({ children, className = '', disabled, ...rest }) {
  return (
    <button
      disabled={disabled}
      className={`w-full h-14 rounded-full bg-primary text-on-primary font-heading font-bold text-[15px] shadow-md active:scale-[0.98] transition disabled:opacity-40 disabled:active:scale-100 ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function GhostButton({ children, className = '', ...rest }) {
  return (
    <button
      className={`w-full h-14 rounded-full bg-transparent border-2 border-primary text-primary font-heading font-bold text-[15px] active:scale-[0.98] transition ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function ChipButton({ children, active, className = '', ...rest }) {
  return (
    <button
      className={`px-4 py-2 rounded-full font-heading font-semibold text-[13px] border-2 transition active:scale-95 ${
        active
          ? 'bg-primary text-on-primary border-primary'
          : 'bg-surface text-primary border-primary/40 hover:bg-primary/5'
      } ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
