import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton, GhostButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { getApplicationsByUser, getSessionState } from '../api/client';

export default function Home() {
  const navigate = useNavigate();
  const { applicationId, sessionId, patch } = useApp();
  const [mobile, setMobile] = useState('');
  const [lookupResults, setLookupResults] = useState(null);
  const [error, setError] = useState(null);

  const resume = async () => {
    if (!sessionId) return navigate('/status');
    try {
      const state = await getSessionState(sessionId);
      if (state.application.status === 'APPROVED') navigate('/success');
      else if (['UNDER_REVIEW', 'ACTION_NEEDED'].includes(state.application.status)) navigate('/status');
      else navigate('/chat');
    } catch {
      navigate('/status');
    }
  };

  const lookup = async () => {
    setError(null);
    try {
      const res = await getApplicationsByUser(mobile);
      setLookupResults(res.applications || []);
    } catch (e) {
      setError('Lookup failed.');
    }
  };

  return (
    <PhoneScreen title="YONO 3.0">
      <div className="flex flex-col items-center text-center gap-3 mb-6">
        <div className="w-16 h-16 rounded-2xl bg-primary text-on-primary flex items-center justify-center text-2xl font-heading font-bold">
          Y
        </div>
        <h1 className="font-heading font-bold text-xl text-primary">Welcome back</h1>
      </div>

      <div className="flex flex-col gap-3 mb-6">
        {applicationId ? (
          <PrimaryButton onClick={resume}>Resume my application</PrimaryButton>
        ) : (
          <PrimaryButton onClick={() => navigate('/')}>Open a new account</PrimaryButton>
        )}
        <GhostButton onClick={() => navigate('/finguru')}>Ask FinGuru 💡</GhostButton>
        <GhostButton onClick={() => navigate('/game')}>Play Savings Quest 🎮</GhostButton>
        <GhostButton onClick={() => navigate('/support')}>Support</GhostButton>
      </div>

      <div className="bg-surface-container-low rounded-xl p-4">
        <p className="text-[12.5px] font-semibold text-on-surface-variant mb-2">Look up applications by mobile</p>
        <div className="flex gap-2">
          <input
            value={mobile}
            onChange={(e) => setMobile(e.target.value)}
            placeholder="9876543210"
            className="flex-1 h-10 rounded-lg bg-surface-lowest border border-outline-variant px-3 text-[13px]"
          />
          <button onClick={lookup} className="px-3 h-10 rounded-lg bg-primary text-on-primary text-[12px] font-bold">
            Find
          </button>
        </div>
        {error && <p className="text-error text-[12px] mt-2">{error}</p>}
        {lookupResults && (
          <ul className="mt-3 flex flex-col gap-2">
            {lookupResults.length === 0 && <p className="text-[12px] text-on-surface-variant">No applications found.</p>}
            {lookupResults.map((a) => (
              <li key={a.id} className="bg-surface-lowest rounded-lg p-2 flex items-center justify-between">
                <div>
                  <p className="text-[12.5px] font-semibold">{a.product_id}</p>
                  <p className="text-[11px] text-on-surface-variant">{a.status}</p>
                </div>
                <button
                  className="text-primary text-[12px] font-bold underline"
                  onClick={() => {
                    patch({ applicationId: a.id });
                    navigate('/status');
                  }}
                >
                  View
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </PhoneScreen>
  );
}
