import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { useT } from '../lib/i18n';

const CLAUSES = [
  {
    key: 'terms',
    title: 'Terms & Conditions',
    text: 'I agree to SBI YONO 3.0\'s account opening terms, applicable service charges, and the schedule of fees for the selected product.',
  },
  {
    key: 'data_sharing',
    title: 'Data usage & KYC sharing',
    text: 'I consent to SBI verifying my PAN, Aadhaar-linked KYC records and uploaded documents with relevant government/regulatory systems (CKYC, NSDL) for identity verification.',
  },
  {
    key: 'communication',
    title: 'Communication consent',
    text: 'I agree to receive account-related communication via SMS, WhatsApp, email and in-app notifications, including OTPs and status updates.',
  },
];

export default function ConsentLegalDetails() {
  const t = useT();
  const navigate = useNavigate();
  const { patch } = useApp();
  const [checked, setChecked] = useState({ terms: false, data_sharing: false, communication: false });

  const allChecked = Object.values(checked).every(Boolean);

  return (
    <PhoneScreen
      title={t('Terms & consent')}
      footer={
        <PrimaryButton
          disabled={!allChecked}
          onClick={() => {
            patch({ pendingConsentPurposes: Object.keys(checked).filter((k) => checked[k]) });
            navigate('/requirements');
          }}
        >
          {t('Accept & continue')}
        </PrimaryButton>
      }
    >
      <p className="text-on-surface-variant text-sm mb-4">
        {t('Please review and accept the following before we start your application.')}
      </p>
      <div className="flex flex-col gap-3">
        {CLAUSES.map((c) => (
          <label
            key={c.key}
            className="flex items-start gap-3 bg-surface-container-low rounded-xl p-4 cursor-pointer border border-transparent has-[:checked]:border-primary"
          >
            <input
              type="checkbox"
              className="mt-1 w-5 h-5 accent-[#00386b] shrink-0"
              checked={checked[c.key]}
              onChange={(e) => setChecked((prev) => ({ ...prev, [c.key]: e.target.checked }))}
            />
            <div>
              <p className="font-heading font-bold text-[14px] text-on-surface">{t(c.title)}</p>
              <p className="text-[12.5px] text-on-surface-variant mt-1 leading-snug">{t(c.text)}</p>
            </div>
          </label>
        ))}
      </div>
    </PhoneScreen>
  );
}
