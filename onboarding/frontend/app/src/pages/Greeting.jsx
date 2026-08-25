import { useNavigate } from 'react-router-dom';
import { PrimaryButton, GhostButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

export default function Greeting() {
  const t = useT();
  const navigate = useNavigate();
  const { reset } = useApp();

  return (
    <div className="flex flex-col min-h-screen bg-surface px-6 py-10 justify-between">
      <div className="flex flex-col items-center text-center gap-4 mt-10">
        <div className="w-20 h-20 rounded-3xl bg-primary text-on-primary flex items-center justify-center text-3xl font-heading font-bold shadow-lg">
          Y
        </div>
        <h1 className="font-heading font-bold text-3xl text-primary leading-tight mt-2">
          {t('Hey there')} 👋
          <br />
          {t('Welcome to YONO 3.0')}
        </h1>
        <p className="text-on-surface-variant text-[15px] max-w-xs">
          {t('Open a bank account in a few minutes — just chat with us, no forms, no branch visits.')}
        </p>
      </div>

      <div className="flex flex-col gap-4 items-center">
        <div className="w-full h-48 rounded-2xl bg-primary-container/10 border border-primary/10 flex items-center justify-center text-6xl">
          💬🏦
        </div>
        <div className="w-full flex flex-col gap-3">
          <PrimaryButton
            onClick={() => {
              reset();
              navigate('/language');
            }}
          >
            {t("Let's get started")}
          </PrimaryButton>
          <GhostButton onClick={() => navigate('/home')}>{t('I already have an application')}</GhostButton>
        </div>
      </div>
    </div>
  );
}
