import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton, GhostButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

export default function DuplicateUser() {
  const t = useT();
  const navigate = useNavigate();
  const { application } = useApp();

  return (
    <PhoneScreen>
      <div className="flex flex-col items-center text-center gap-5 mt-16">
        <div className="w-24 h-24 rounded-full bg-tertiary-container/15 flex items-center justify-center text-5xl">
          🙂
        </div>
        <h1 className="font-heading font-bold text-2xl text-primary">{t("You're already with us!")}</h1>
        <p className="text-on-surface-variant text-[14.5px] max-w-xs">
          {t('Looks like you already have an approved account for this product')}
          {application?.product_id ? ` (${application.product_id.replaceAll('_', ' ')})` : ''}. {t('No need to apply again.')}
        </p>
        {application && (
          <div className="w-full bg-surface-container-low rounded-xl p-4 text-left">
            <p className="text-[12px] text-on-surface-variant">{t('Application ref')}</p>
            <p className="font-mono text-[13px] text-on-surface">{application.id}</p>
            <p className="text-[12px] text-on-surface-variant mt-2">{t('Status')}</p>
            <p className="font-heading font-bold text-primary text-[13px]">{application.status}</p>
          </div>
        )}
        <div className="w-full flex flex-col gap-3 mt-4">
          <PrimaryButton onClick={() => navigate('/status')}>{t('View my account status')}</PrimaryButton>
          <GhostButton onClick={() => navigate('/')}>{t('Start a different application')}</GhostButton>
        </div>
      </div>
    </PhoneScreen>
  );
}
