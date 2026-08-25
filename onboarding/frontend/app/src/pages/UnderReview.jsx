import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

export default function UnderReview() {
  const t = useT();
  const navigate = useNavigate();
  const { applicationId } = useApp();

  if (!applicationId) {
    navigate('/');
    return null;
  }

  return (
    <PhoneScreen>
      <div className="flex flex-col items-center text-center gap-5 mt-16">
        <div className="w-24 h-24 rounded-full bg-primary-container/15 flex items-center justify-center text-5xl animate-pulse">
          ⏳
        </div>
        <h1 className="font-heading font-bold text-2xl text-primary">{t('Application submitted!')}</h1>
        <p className="text-on-surface-variant text-[14.5px] max-w-xs">
          {t("Thanks — we're reviewing your details now. This usually takes just a few seconds in this demo (1-2 business days in production).")}
        </p>
        <p className="text-[12px] font-mono bg-surface-container-low px-3 py-1 rounded-full text-on-surface-variant">
          Ref: #{applicationId.slice(0, 8).toUpperCase()}
        </p>
        <PrimaryButton className="mt-4" onClick={() => navigate('/status')}>
          {t('Track my application')}
        </PrimaryButton>
      </div>
    </PhoneScreen>
  );
}
