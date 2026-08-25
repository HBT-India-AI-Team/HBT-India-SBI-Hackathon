import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

const OPTIONS = [
  { icon: '💬', title: 'Chat with a human agent', desc: 'Escalate to our support team in-app', to: '/support/chat' },
  { icon: '📞', title: 'Request a call back', desc: 'Mocked call flow — no real telephony in this build', to: '/support/call' },
  { icon: '🟢', title: 'Continue on WhatsApp', desc: 'Get a deep link to continue this application there', to: '/support/whatsapp' },
];

export default function SupportSheet() {
  const t = useT();
  const navigate = useNavigate();
  const { applicationId } = useApp();

  return (
    <PhoneScreen title={t('Connect with support')}>
      <p className="text-on-surface-variant text-sm mb-5">{t('How would you like to get help with your application?')}</p>
      <div className="flex flex-col gap-3">
        {OPTIONS.map((o) => (
          <button
            key={o.to}
            onClick={() => navigate(o.to)}
            disabled={!applicationId}
            className="text-left flex items-center gap-4 bg-surface-lowest border border-outline-variant/30 rounded-xl p-4 shadow-sm active:scale-[0.98] transition disabled:opacity-40"
          >
            <div className="w-12 h-12 rounded-full bg-primary-container/15 flex items-center justify-center text-2xl shrink-0">
              {o.icon}
            </div>
            <div>
              <p className="font-heading font-bold text-[14.5px] text-on-surface">{t(o.title)}</p>
              <p className="text-[12.5px] text-on-surface-variant">{t(o.desc)}</p>
            </div>
          </button>
        ))}
      </div>
      {!applicationId && (
        <p className="text-[12px] text-error mt-3">{t('Start an application first to reach support about it.')}</p>
      )}
    </PhoneScreen>
  );
}
