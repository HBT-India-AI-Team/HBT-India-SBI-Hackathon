import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { PrimaryButton } from '../components/PrimaryButton';
import { useApp } from '../context/AppContext';
import { createHandoff } from '../api/client';

export default function WhatsAppHandoff() {
  const navigate = useNavigate();
  const { applicationId } = useApp();
  const [res, setRes] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!applicationId) return;
    createHandoff(applicationId, 'whatsapp')
      .then(setRes)
      .catch((e) => setError(e?.response?.data?.detail || 'Could not create WhatsApp handoff link.'));
  }, [applicationId]);

  if (!applicationId) {
    navigate('/support');
    return null;
  }

  return (
    <PhoneScreen title="Continue on WhatsApp">
      <div className="flex flex-col items-center text-center gap-4 mt-6">
        <div className="w-20 h-20 rounded-full bg-[#25D366]/15 flex items-center justify-center text-4xl">🟢</div>
        <h2 className="font-heading font-bold text-lg text-primary">Pick up where you left off</h2>
        <p className="text-on-surface-variant text-[14px] max-w-xs">
          We generated a secure deep link (real call to POST /applications/{'{id}'}/handoff/whatsapp) that resumes
          this exact application inside WhatsApp.
        </p>
      </div>

      {error && <p className="text-error text-[12.5px] text-center mt-4">{error}</p>}

      {res && (
        <div className="mt-6 flex flex-col gap-3">
          <div className="bg-surface-container-low rounded-xl p-4">
            <p className="text-[12px] text-on-surface-variant mb-1">Deep link</p>
            <p className="text-[12px] font-mono break-all text-primary">{res.link}</p>
            <p className="text-[11px] text-on-surface-variant mt-2">
              Expires in {Math.round(res.expires_in_seconds / 60)} minutes
            </p>
          </div>
          <a href={res.link} target="_blank" rel="noreferrer">
            <PrimaryButton>Open in WhatsApp</PrimaryButton>
          </a>
        </div>
      )}
    </PhoneScreen>
  );
}
