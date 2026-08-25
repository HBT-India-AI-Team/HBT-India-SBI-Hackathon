import { useNavigate } from 'react-router-dom';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

export default function Success() {
  const t = useT();
  const navigate = useNavigate();
  const { applicationId } = useApp();

  return (
    <div className="flex flex-col min-h-screen bg-surface px-6 py-12 justify-between">
      <div className="flex flex-col items-center text-center gap-5 mt-10">
        <div className="w-28 h-28 rounded-full bg-success-container flex items-center justify-center text-6xl">
          🎉
        </div>
        <h1 className="font-heading font-bold text-2xl text-primary">{t("You're all set!")}</h1>
        <p className="text-on-surface-variant text-[14.5px] max-w-xs">
          {t('Your account has been approved. Welcome to the SBI YONO family — you can now log in and start banking.')}
        </p>
        {applicationId && (
          <p className="text-[12px] font-mono bg-surface-container-low px-3 py-1 rounded-full text-on-surface-variant">
            Ref: #{applicationId.slice(0, 8).toUpperCase()}
          </p>
        )}
      </div>
      <div className="w-full flex flex-col gap-3">
        <PrimaryButton onClick={() => navigate('/home')}>{t('Go to YONO Home')}</PrimaryButton>
      </div>
    </div>
  );
}
