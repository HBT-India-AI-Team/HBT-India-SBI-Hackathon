import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { postConsent, startApplication } from '../api/client';
import { useT } from '../lib/i18n';

const PREVIEW = {
  savings_account: ['Mobile number (OTP)', 'PAN verification', 'Upload PAN card photo', 'Confirm product', 'Review & submit'],
  msme_current_account: [
    'Mobile number (OTP)',
    'Signatory PAN',
    'Business PAN',
    'GSTIN verification',
    'Authorized signatory details',
    'Upload PAN card photo',
    'Upload GST certificate',
    'Confirm product',
    'Review & submit',
  ],
  minor_savings_account: [
    "Minor's mobile number (OTP)",
    'Guardian consent',
    "Guardian's mobile number (OTP)",
    'Upload guardian ID proof',
    'Confirm product',
    'Review & submit',
  ],
};

export default function RequirementsChecklist() {
  const t = useT();
  const navigate = useNavigate();
  const { productId, language, pendingConsentPurposes, mobileNumber, patch } = useApp();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [mobile, setMobile] = useState(mobileNumber || '');
  const steps = PREVIEW[productId] || PREVIEW.savings_account;

  const begin = async () => {
    setBusy(true);
    setError(null);
    try {
      patch({ mobileNumber: mobile || null });
      const res = await startApplication({
        product_id: productId || 'savings_account',
        channel: 'web',
        language: language || 'en',
        mobile_number: mobile || null,
      });

      if (res.duplicate_detected) {
        patch({ application: res.application, applicationId: res.application.id });
        navigate('/duplicate');
        return;
      }

      const applicationId = res.application.id;
      patch({ applicationId, sessionId: res.session_id, application: res.application });

      for (const purpose of pendingConsentPurposes || []) {
        try {
          await postConsent(applicationId, { purpose, granted: true });
        } catch {
          /* non-fatal */
        }
      }

      navigate('/onboarding');
    } catch (e) {
      setError(e?.response?.data?.detail || t('Could not start your application. Please try again.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PhoneScreen
      title={t("What you'll need")}
      footer={
        <div>
          {error && <p className="text-error text-[12.5px] mb-2">{error}</p>}
          <PrimaryButton onClick={begin} disabled={busy}>
            {busy ? t('Starting…') : t("Let's start")}
          </PrimaryButton>
        </div>
      }
    >
      <p className="text-on-surface-variant text-sm mb-4">
        {t('Quick overview before we dive in — you can complete these in any order the chat suggests.')}
      </p>
      <div className="mb-4">
        <label className="text-[12px] font-semibold text-on-surface-variant">
          {t('Mobile number on file? (optional — lets us detect an existing/duplicate application for you)')}
        </label>
        <input
          value={mobile}
          onChange={(e) => setMobile(e.target.value)}
          placeholder="9876543210"
          className="w-full mt-1 h-11 rounded-xl bg-surface-container-low border border-outline-variant px-4 text-[13.5px]"
        />
      </div>
      <ul className="flex flex-col gap-3">
        {steps.map((s, i) => (
          <li key={s} className="flex items-center gap-3 bg-surface-container-low rounded-xl p-3.5">
            <span className="w-7 h-7 rounded-full bg-primary-container/15 text-primary flex items-center justify-center text-[12px] font-bold shrink-0">
              {i + 1}
            </span>
            <span className="text-[13.5px] text-on-surface">{t(s)}</span>
          </li>
        ))}
      </ul>
    </PhoneScreen>
  );
}
