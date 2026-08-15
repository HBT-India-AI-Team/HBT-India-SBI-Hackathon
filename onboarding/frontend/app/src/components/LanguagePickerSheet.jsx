import { PrimaryButton } from './PrimaryButton';

// Matches the stitch_yono_3.0_proto_changes "language_picker" wireframe: a
// bottom sheet over a dimmed backdrop, radio list with native-script initial
// avatars, sticky Done button. Kept separate from pages/LanguagePicker.jsx
// (the full-page step in the onboarding funnel) -- this is the reopenable
// sheet used from Home's language toggle, a different flow.
const LANGUAGES = [
  { code: 'en', native: 'English', english: 'English', initial: 'A' },
  { code: 'hi', native: 'हिन्दी', english: 'Hindi', initial: 'अ' },
  { code: 'ta', native: 'தமிழ்', english: 'Tamil', initial: 'அ' },
  { code: 'te', native: 'తెలుగు', english: 'Telugu', initial: 'అ' },
  { code: 'bn', native: 'বাংলা', english: 'Bengali', initial: 'অ' },
  { code: 'ml', native: 'മലയാളം', english: 'Malayalam', initial: 'അ' },
];

export default function LanguagePickerSheet({ open, value, onSelect, onClose }) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end max-w-[480px] mx-auto left-0 right-0">
      <button
        aria-label="Close language picker"
        onClick={onClose}
        className="absolute inset-0 bg-on-surface/40 transition-opacity"
      />
      <div className="relative bg-surface rounded-t-2xl shadow-[0_-4px_20px_rgba(0,56,107,0.15)] flex flex-col max-h-[85vh]">
        <div className="w-full flex justify-center pt-3 pb-2">
          <div className="w-12 h-1.5 bg-outline-variant rounded-full" />
        </div>
        <div className="px-5 py-3 flex items-center justify-between border-b border-surface-highest">
          <h2 className="font-heading font-bold text-[19px] text-on-surface">Choose your language</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="w-8 h-8 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container active:scale-95 transition"
          >
            <CloseIcon />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-3">
          {LANGUAGES.map((l) => {
            const selected = value === l.code;
            return (
              <label
                key={l.code}
                className={`flex items-center justify-between p-4 rounded-xl border-2 cursor-pointer transition-all ${
                  selected ? 'border-primary bg-primary-fixed/40' : 'border-outline-variant bg-surface-lowest'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-10 h-10 rounded-full flex items-center justify-center font-heading font-bold text-[16px] shrink-0 ${
                      selected ? 'bg-primary-container text-on-primary-container' : 'bg-surface-high text-on-surface'
                    }`}
                  >
                    {l.initial}
                  </div>
                  <div>
                    <span className="block font-body text-[15px] text-on-surface">{l.native}</span>
                    <span className="block font-body text-[12px] text-on-surface-variant">{l.english}</span>
                  </div>
                </div>
                <input
                  type="radio"
                  name="language"
                  value={l.code}
                  checked={selected}
                  onChange={() => onSelect(l.code)}
                  className="sr-only"
                />
                <div
                  className={`w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors shrink-0 ${
                    selected ? 'border-primary bg-primary' : 'border-outline-variant'
                  }`}
                >
                  {selected && <CheckIcon />}
                </div>
              </label>
            );
          })}
        </div>
        <div className="px-5 py-4 bg-surface border-t border-surface-highest">
          <PrimaryButton onClick={onClose}>Done</PrimaryButton>
        </div>
      </div>
    </div>
  );
}

function CloseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
      <path d="M20 6L9 17l-5-5" stroke="var(--color-on-primary)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
