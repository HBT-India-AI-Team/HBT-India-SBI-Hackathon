import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton, GhostButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

export default function ConsentMoment() {
  const t = useT();
  const navigate = useNavigate();
  const { patch } = useApp();

  return (
    <PhoneScreen
      title={t('Before we begin')}
      footer={
        <div className="flex flex-col gap-3">
          <PrimaryButton
            onClick={() => {
              patch({ pendingConsent: true });
              navigate('/consent/legal');
            }}
          >
            {t('I agree, continue')}
          </PrimaryButton>
          <GhostButton onClick={() => navigate(-1)}>{t('Not now')}</GhostButton>
        </div>
      }
    >
      <div className="flex flex-col items-center text-center gap-4 mt-6">
        <div className="w-16 h-16 rounded-full bg-primary-container/15 flex items-center justify-center text-3xl">
          🔒
        </div>
        <h2 className="font-heading font-bold text-xl text-primary">{t('Your data, your control')}</h2>
        <p className="text-on-surface-variant text-[14.5px] max-w-xs">
          {t("To open your account we'll need to verify your mobile number, PAN and a few documents. We only use this information for KYC and account opening, in line with RBI guidelines.")}
        </p>
      </div>
      <div className="mt-6 flex flex-col gap-3">
        {[
          ['📱', 'Mobile number', 'For OTP verification & account communication'],
          ['🪪', 'PAN & ID documents', 'For identity verification (KYC)'],
          ['🏦', 'Account preferences', 'To set up the right product for you'],
        ].map(([icon, title, desc]) => (
          <div key={title} className="flex items-start gap-3 bg-surface-container-low rounded-xl p-3.5">
            <span className="text-xl">{icon}</span>
            <div>
              <p className="font-semibold text-[13.5px] text-on-surface">{t(title)}</p>
              <p className="text-[12.5px] text-on-surface-variant">{t(desc)}</p>
            </div>
          </div>
        ))}
      </div>
    </PhoneScreen>
  );
}
