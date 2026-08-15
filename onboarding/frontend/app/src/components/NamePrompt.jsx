import { useState } from 'react';
import PhoneScreen from './PhoneScreen';
import { PrimaryButton } from './PrimaryButton';

// "What should I call you?" -- shown whenever FinGuru has no resolved name
// (no ?name= hand-off and nothing in localStorage yet). Single text input,
// non-empty validation only, per spec -- no full form.
export default function NamePrompt({ onSubmit }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState(false);

  const submit = (e) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) {
      setError(true);
      return;
    }
    onSubmit(trimmed);
  };

  return (
    <PhoneScreen title="Welcome">
      <form onSubmit={submit} className="flex flex-col gap-4 mt-10">
        <div className="flex flex-col items-center text-center gap-2 mb-2">
          <div className="w-14 h-14 rounded-2xl bg-primary-container text-white flex items-center justify-center text-2xl">
            💡
          </div>
          <h1 className="font-heading font-bold text-lg text-primary">What should I call you?</h1>
          <p className="text-[12.5px] text-on-surface-variant">So FinGuru can pick up where you left off.</p>
        </div>
        <input
          autoFocus
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError(false);
          }}
          placeholder="Your name"
          aria-label="Your name"
          className="h-12 rounded-xl bg-surface-container-low border border-outline-variant px-4 text-[15px] text-on-surface placeholder:text-outline outline-none focus:border-primary"
        />
        {error && <p className="text-error text-[12px] text-center -mt-2">Please enter a name.</p>}
        <PrimaryButton type="submit">Continue</PrimaryButton>
      </form>
    </PhoneScreen>
  );
}
