import { useNavigate } from 'react-router-dom';
import { useT } from '../lib/i18n';

export default function PhoneScreen({ title, onBack, right, children, footer, noPad }) {
  const navigate = useNavigate();
  const t = useT();
  return (
    <div className="flex flex-col min-h-screen bg-surface">
      {title !== undefined && (
        <header className="sticky top-0 z-40 bg-surface/95 backdrop-blur border-b border-surface-highest">
          <div className="flex items-center justify-between px-5 h-14">
            <button
              onClick={onBack || (() => navigate(-1))}
              className="w-9 h-9 rounded-full flex items-center justify-center text-primary hover:bg-surface-container active:scale-95 transition"
              aria-label={t('Back')}
            >
              <ArrowIcon />
            </button>
            <h1 className="font-heading font-bold text-[18px] text-primary flex-1 text-center truncate px-2">
              {title}
            </h1>
            <div className="min-w-9 h-9 flex items-center justify-end gap-1.5 shrink-0">{right}</div>
          </div>
        </header>
      )}
      <main className={`flex-1 flex flex-col ${noPad ? '' : 'px-5 py-5'} ${footer ? 'pb-28' : ''}`}>
        {children}
      </main>
      {footer && (
        <div className="fixed bottom-0 left-1/2 -translate-x-1/2 w-full max-w-[480px] bg-surface/95 backdrop-blur border-t border-surface-highest px-5 py-4 z-40">
          {footer}
        </div>
      )}
    </div>
  );
}

function ArrowIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path d="M15 18l-6-6 6-6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
