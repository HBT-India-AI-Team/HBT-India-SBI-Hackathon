import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { translate } from '../lib/i18n';

const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी (Hindi)' },
  { code: 'ta', label: 'தமிழ் (Tamil)' },
  { code: 'te', label: 'తెలుగు (Telugu)' },
  { code: 'bn', label: 'বাংলা (Bengali)' },
  { code: 'mr', label: 'मराठी (Marathi)' },
];

export default function LanguagePicker() {
  const navigate = useNavigate();
  const { patch, language } = useApp();
  const [selected, setSelected] = useState(language || 'en');
  // Previews the language being CHOSEN rather than the one already saved,
  // so tapping हिन्दी switches this screen immediately instead of only
  // taking effect on the next one. The saved value is written on Continue.
  const t = (text) => translate(text, selected);

  return (
    <PhoneScreen
      title={t('Choose your language')}
      footer={
        <PrimaryButton
          onClick={() => {
            patch({ language: selected });
            navigate('/product');
          }}
        >
          {t('Continue')}
        </PrimaryButton>
      }
    >
      <p className="text-on-surface-variant text-sm mb-4">{t('You can change this anytime in chat.')}</p>
      <div className="flex flex-col gap-3">
        {LANGUAGES.map((l) => (
          <button
            key={l.code}
            onClick={() => setSelected(l.code)}
            className={`w-full text-left px-4 py-4 rounded-xl border-2 font-heading font-semibold text-[15px] transition ${
              selected === l.code
                ? 'border-primary bg-primary-container/10 text-primary'
                : 'border-outline-variant text-on-surface bg-surface-lowest'
            }`}
          >
            {l.label}
          </button>
        ))}
      </div>
    </PhoneScreen>
  );
}
