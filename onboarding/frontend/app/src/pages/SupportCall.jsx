import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { useApp } from '../context/AppContext';
import { API_BASE, callEnd, callInitiate } from '../api/client';
import useLiveCall from '../hooks/useLiveCall';
import { useT } from '../lib/i18n';

export default function SupportCall() {
  const t = useT();
  const navigate = useNavigate();
  const { sessionId } = useApp();
  // mode: 'live-connecting' | 'live-active' | 'mock-connecting' | 'mock-active' | 'ended'
  const [mode, setMode] = useState('live-connecting');
  const [fallbackReason, setFallbackReason] = useState(null);
  const [seconds, setSeconds] = useState(0);
  const endedRef = useRef(false);
  const transcriptEndRef = useRef(null);

  const handleFallback = (reason) => {
    if (endedRef.current) return;
    setFallbackReason(reason);
    setMode('mock-connecting');
  };

  const live = useLiveCall({ apiBase: API_BASE, sessionId, onFallback: handleFallback });

  // Kick off: create the call record (real, mocked-telephony) then attempt
  // the real live-audio WS. If the WS never reaches "live" within a short
  // grace period, or fails outright, we fall back to the mocked call UI.
  useEffect(() => {
    if (!sessionId) return;
    callInitiate(sessionId).catch(() => {});
    live.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Reflect the live hook's own status into our mode state machine.
  useEffect(() => {
    if (endedRef.current) return;
    if (live.status === 'live') setMode('live-active');
    if (live.status === 'ended' && mode !== 'ended') {
      setMode('ended');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.status]);

  // Mock-flow timers: only run once we've fallen back.
  useEffect(() => {
    if (mode !== 'mock-connecting') return;
    const t = setTimeout(() => setMode('mock-active'), 2500);
    return () => clearTimeout(t);
  }, [mode]);

  useEffect(() => {
    if (mode !== 'mock-active' && mode !== 'live-active') return;
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [mode]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [live.transcript]);

  const hangUp = async () => {
    endedRef.current = true;
    setMode('ended');
    live.end();
    if (sessionId) await callEnd(sessionId).catch(() => {});
  };

  const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;

  if (!sessionId) {
    navigate('/support');
    return null;
  }

  const isConnecting = mode === 'live-connecting' || mode === 'mock-connecting';
  const isActive = mode === 'live-active' || mode === 'mock-active';
  const isLive = mode === 'live-active';

  return (
    <PhoneScreen title={t('Support call')}>
      <p className="text-[11px] text-on-surface-variant bg-surface-container-low rounded-lg p-2 mb-4 text-center">
        {isLive
          ? 'LIVE: connected to the real voice server over a live audio call.'
          : fallbackReason
          ? `MOCKED (live call unavailable — ${fallbackReason}): only exercises the backend's mock call-state endpoints (POST /sessions/{id}/call/initiate and /call/end).`
          : "MOCKED: no real telephony integration — this only exercises the backend's mock call-state endpoints (POST /sessions/{id}/call/initiate and /call/end)."}
      </p>
      <div className="flex flex-col items-center text-center gap-5 mt-6 flex-1">
        <div className="relative w-24 h-24 flex items-center justify-center">
          {isConnecting && (
            <>
              <span className="absolute inset-0 rounded-full border-2 border-primary/30 animate-ping" />
            </>
          )}
          <div className="w-24 h-24 rounded-full bg-primary-container/15 flex items-center justify-center text-5xl relative z-10">
            🎧
          </div>
        </div>
        <h2 className="font-heading font-bold text-xl text-primary">
          {isLive ? t('YONO Support (live)') : t('SBI Support')}
        </h2>
        {mode === 'live-connecting' && <p className="text-on-surface-variant animate-pulse">{t('Connecting…')}</p>}
        {mode === 'mock-connecting' && <p className="text-on-surface-variant animate-pulse">{t('Connecting…')}</p>}
        {isActive && <p className="text-on-surface-variant font-mono text-lg">{fmt(seconds)}</p>}
        {mode === 'ended' && <p className="text-on-surface-variant">{t('Call ended')}</p>}

        {isLive && live.transcript.length > 0 && (
          <div className="w-full max-h-56 overflow-y-auto flex flex-col gap-2 bg-surface-container-low rounded-lg p-3 text-left">
            {live.transcript.map((line, i) => (
              <p key={i} className="text-[13px]">
                <span className={`font-semibold ${line.from === 'user' ? 'text-primary' : 'text-tertiary'}`}>
                  {line.from === 'user' ? 'You: ' : 'Agent: '}
                </span>
                <span className="text-on-surface-variant">{line.text}</span>
              </p>
            ))}
            <div ref={transcriptEndRef} />
          </div>
        )}

        {isLive && (
          <button
            onClick={live.toggleMute}
            aria-label={live.muted ? t('Unmute microphone') : t('Mute microphone')}
            className={`h-10 px-4 rounded-full text-[13px] font-heading font-bold flex items-center gap-2 border transition ${
              live.muted
                ? 'bg-error-container text-on-error-container border-error'
                : 'bg-surface-container-low text-on-surface-variant border-outline-variant'
            }`}
          >
            {live.muted ? '🔇 Unmute' : '🎙️ Mute'}
          </button>
        )}

        {mode !== 'ended' && (
          <button
            onClick={hangUp}
            aria-label="End call"
            className="mt-8 w-16 h-16 rounded-full bg-error text-on-error flex items-center justify-center text-2xl shadow-lg active:scale-95 transition"
          >
            📵
          </button>
        )}
        {mode === 'ended' && (
          <button
            onClick={() => navigate('/status')}
            className="mt-10 h-12 px-6 rounded-full bg-primary text-on-primary font-heading font-bold text-[14px]"
          >
            Back to my application
          </button>
        )}
      </div>
    </PhoneScreen>
  );
}
