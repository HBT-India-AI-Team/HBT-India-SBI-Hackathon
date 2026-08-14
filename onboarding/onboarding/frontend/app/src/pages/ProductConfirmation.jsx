import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';

const PRODUCTS = [
  {
    id: 'savings_account',
    name: 'Zero Balance Digital Savings Account',
    tag: 'Recommended',
    icon: '💳',
    perks: ['No monthly maintenance fees', 'Instant virtual debit card', 'Earn 3.5% interest p.a.'],
  },
  {
    id: 'msme_current_account',
    name: 'MSME Current Account',
    tag: 'For businesses',
    icon: '🏢',
    perks: ['GSTIN-linked business banking', 'Free digital invoicing tools', 'Dedicated relationship support'],
  },
  {
    id: 'minor_savings_account',
    name: 'Minor Savings Account',
    tag: 'Guardian-operated',
    icon: '🧒',
    perks: ['For under-18 account holders', 'Guardian consent + co-verification', 'Great for pocket-money & goals'],
  },
];

export default function ProductConfirmation() {
  const navigate = useNavigate();
  const { patch, productId } = useApp();
  const [selected, setSelected] = useState(productId || 'savings_account');

  return (
    <PhoneScreen
      title="Open Account"
      footer={
        <PrimaryButton
          onClick={() => {
            patch({ productId: selected });
            navigate('/consent');
          }}
        >
          Yes, this one
        </PrimaryButton>
      }
    >
      <div className="flex items-end gap-2 self-start max-w-[90%] mb-5">
        <div className="w-9 h-9 rounded-full bg-primary-container text-white flex items-center justify-center shrink-0">
          🤖
        </div>
        <div className="bg-surface-container-low p-3.5 rounded-2xl rounded-bl-sm border border-surface-highest text-[14.5px]">
          Based on what you're looking for, here are the accounts we can open for you today. Pick one to continue.
        </div>
      </div>
      <div className="flex flex-col gap-4">
        {PRODUCTS.map((p) => (
          <button
            key={p.id}
            onClick={() => setSelected(p.id)}
            className={`text-left rounded-xl p-4 border-2 transition bg-surface-lowest ${
              selected === p.id ? 'border-primary shadow-md' : 'border-outline-variant/40'
            }`}
          >
            <div className="flex items-start justify-between mb-2">
              <div>
                <span className="text-[11px] font-bold uppercase tracking-wider text-primary">{p.tag}</span>
                <h2 className="font-heading font-bold text-[17px] text-on-surface">{p.name}</h2>
              </div>
              <div className="w-11 h-11 rounded-full bg-primary-container/15 flex items-center justify-center text-xl shrink-0">
                {p.icon}
              </div>
            </div>
            <ul className="flex flex-col gap-1 mt-2">
              {p.perks.map((perk) => (
                <li key={perk} className="text-[13px] text-on-surface-variant flex items-center gap-2">
                  <span className="text-primary">✓</span> {perk}
                </li>
              ))}
            </ul>
          </button>
        ))}
      </div>
    </PhoneScreen>
  );
}
