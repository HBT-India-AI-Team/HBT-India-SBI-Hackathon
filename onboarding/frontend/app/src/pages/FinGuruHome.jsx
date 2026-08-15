import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import NamePrompt from '../components/NamePrompt';
import { useFinGuruName } from '../lib/finguruIdentity';

// The FinGuru agent is a single-shot Q&A backend with no trending/discovery
// feeds, so we surface a curated set of starter prompts instead.
const STARTERS = [
  'What is a SIP and how do I start one?',
  'How much should I keep in an emergency fund?',
  'Old vs new tax regime — which is better for me?',
  'What are the current FD interest rates like?',
  'Explain PPF and its tax benefits',
  'Which government schemes help first-time home buyers?',
  'Should I prepay my home loan or invest?',
  'How do I spot a UPI / investment scam?',
];

export default function FinGuruHome() {
  const navigate = useNavigate();
  const { name, setName } = useFinGuruName();
  const askAbout = (text) => navigate('/finguru/chat', { state: { seed: text } });

  if (!name) return <NamePrompt onSubmit={setName} />;

  return (
    <PhoneScreen
      title="FinGuru"
      right={
        <button
          onClick={() => navigate('/finguru/history')}
          className="w-9 h-9 rounded-full flex items-center justify-center text-[16px] text-primary hover:bg-surface-container"
          aria-label="Conversation history"
          title="Conversation history"
        >
          🕘
        </button>
      }
    >
      <div className="flex flex-col items-center text-center gap-2 mb-6">
        <div className="w-16 h-16 rounded-2xl bg-primary-container text-white flex items-center justify-center text-3xl">
          💡
        </div>
        <h1 className="font-heading font-bold text-xl text-primary">FinGuru</h1>
        <p className="text-[13px] text-on-surface-variant max-w-[280px]">
          Your India-context money guide. Ask about SIPs, PPF, tax, FD rates, loans, government schemes,
          fraud — anything.
        </p>
      </div>

      <PrimaryButton className="mb-6" onClick={() => navigate('/finguru/chat')}>
        Ask FinGuru a question
      </PrimaryButton>

      <section className="mb-4">
        <p className="text-[12.5px] font-semibold text-on-surface-variant mb-2">Try asking</p>
        <div className="flex flex-col gap-2">
          {STARTERS.map((q) => (
            <button
              key={q}
              onClick={() => askAbout(q)}
              className="text-left bg-surface-container-low border border-surface-highest rounded-xl p-3 text-[13.5px] font-semibold text-on-surface active:scale-[0.99] transition"
            >
              {q}
            </button>
          ))}
        </div>
      </section>
    </PhoneScreen>
  );
}
