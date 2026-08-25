import { useT } from '../lib/i18n';
const STATE_META = {
  NOT_STARTED: { label: 'Pending', icon: '○', cls: 'text-outline' },
  AWAITING_INPUT: { label: 'Pending', icon: '○', cls: 'text-outline' },
  SUBMITTED: { label: 'Submitted', icon: '◐', cls: 'text-primary' },
  VERIFYING: { label: 'Verifying…', icon: '◐', cls: 'text-primary' },
  VERIFIED: { label: 'Verified', icon: '✓', cls: 'text-success' },
  REJECTED: { label: 'Action needed', icon: '!', cls: 'text-error' },
  ESCALATED: { label: 'With support', icon: '!', cls: 'text-error' },
};

export default function RequirementsSheet({ open, onClose, requirements = [] }) {
  const t = useT();
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-[480px] bg-surface-lowest rounded-t-2xl p-5 max-h-[75vh] overflow-y-auto shadow-2xl">
        <div className="w-10 h-1.5 bg-outline-variant rounded-full mx-auto mb-4" />
        <h2 className="font-heading font-bold text-lg text-primary mb-4">{t('Requirements checklist')}</h2>
        <ul className="flex flex-col gap-3">
          {requirements.map((r) => {
            const meta = STATE_META[r.state] || STATE_META.NOT_STARTED;
            return (
              <li key={r.id} className="flex items-center gap-3 bg-surface-container-low rounded-xl p-3">
                <span
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold shrink-0 ${meta.cls} border border-current/30`}
                >
                  {meta.icon}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-[13.5px] font-semibold text-on-surface truncate">{t(r.label)}</p>
                  <p className={`text-[12px] ${meta.cls}`}>{t(meta.label)}</p>
                </div>
              </li>
            );
          })}
        </ul>
        <button
          onClick={onClose}
          className="mt-5 w-full h-12 rounded-full border-2 border-primary text-primary font-heading font-bold text-sm"
        >
          {t('Close')}
        </button>
      </div>
    </div>
  );
}
