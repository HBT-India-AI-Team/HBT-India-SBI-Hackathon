import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { createGuardianLink, startApplication } from '../api/client';
import { useT } from '../lib/i18n';

export default function GuardianConsent() {
  const t = useT();
  const navigate = useNavigate();
  const { applicationId, application, language, patch } = useApp();
  const [mobile, setMobile] = useState('');
  const [relationship, setRelationship] = useState('parent');
  const [link, setLink] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const requestLink = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await createGuardianLink(applicationId, { mobile_number: mobile, relationship });
      setLink(res);
    } catch (e) {
      setError(e?.response?.data?.detail || t('Could not create guardian link.'));
    } finally {
      setBusy(false);
    }
  };

  const continueAsGuardian = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await startApplication({
        product_id: application?.product_id || 'minor_savings_account',
        channel: 'web',
        language: language || 'en',
        handoff_token: link.token,
      });
      patch({ guardianSessionId: res.session_id });
      navigate('/guardian/chat');
    } catch (e) {
      setError(e?.response?.data?.detail || t('Could not open guardian session.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PhoneScreen title={t('Guardian consent needed')}>
      <div className="flex flex-col items-center text-center gap-3 mb-6">
        <div className="w-16 h-16 rounded-full bg-primary-container/15 flex items-center justify-center text-3xl">
          🧑‍🤝‍🧑
        </div>
        <h2 className="font-heading font-bold text-lg text-primary">{t('This account needs a guardian')}</h2>
        <p className="text-on-surface-variant text-[14px] max-w-xs">
          {t('Since this is a minor account, we need a parent/guardian to confirm consent and verify their own mobile number before we continue.')}
        </p>
      </div>

      {!link ? (
        <div className="flex flex-col gap-3">
          <div>
            <label className="text-[12px] font-semibold text-on-surface-variant">{t('Guardian mobile number')}</label>
            <input
              value={mobile}
              onChange={(e) => setMobile(e.target.value)}
              placeholder="9876543210"
              className="w-full mt-1 h-12 rounded-xl bg-surface-container-low border border-outline-variant px-4 text-[14px]"
            />
          </div>
          <div>
            <label className="text-[12px] font-semibold text-on-surface-variant">{t('Relationship')}</label>
            <select
              value={relationship}
              onChange={(e) => setRelationship(e.target.value)}
              className="w-full mt-1 h-12 rounded-xl bg-surface-container-low border border-outline-variant px-4 text-[14px]"
            >
              <option value="parent">{t('Parent')}</option>
              <option value="legal_guardian">{t('Legal guardian')}</option>
            </select>
          </div>
          {error && <p className="text-error text-[12.5px]">{error}</p>}
          <PrimaryButton onClick={requestLink} disabled={busy || !mobile}>
            {busy ? t('Generating link…') : t('Send consent link to guardian')}
          </PrimaryButton>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="bg-surface-container-low rounded-xl p-4">
            <p className="text-[12.5px] text-on-surface-variant mb-1">{t('Guardian link generated (mock-sent):')}</p>
            <p className="text-[12px] font-mono break-all text-primary">{link.link}</p>
          </div>
          {error && <p className="text-error text-[12.5px]">{error}</p>}
          <PrimaryButton onClick={continueAsGuardian} disabled={busy}>
            {busy ? t('Opening…') : t('Continue as guardian now (demo)')}
          </PrimaryButton>
          <p className="text-[11.5px] text-on-surface-variant text-center">
            {t('In production the guardian opens this link on their own device — this button simulates that for the demo so the flow can be tested end-to-end in one browser.')}
          </p>
        </div>
      )}
    </PhoneScreen>
  );
}
