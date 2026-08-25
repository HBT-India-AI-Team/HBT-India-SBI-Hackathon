import { useCallback, useRef, useState } from 'react';
import { PCMFrameBuffer, floatTo16kPCM16 } from '../lib/liveCall';
import { logMicStart, logSttSent, logSttReceived, debugLog } from '../lib/pipelineLog';
import { wsUrl } from '../lib/basePath';

// Same-origin WS that the Vite dev proxy forwards straight to Sarvam's own
// realtime STT endpoint (wss://api.sarvam.ai/speech-to-text-realtime/ws),
// NOT to the voice server -- its outbound path to Sarvam fails (corporate
// TLS-inspection proxy on that machine rejects Sarvam's cert chain); the
// browser's own network has no such interception. The proxy attaches the
// subscription key server-side (browsers can't set custom WS handshake
// headers), so the browser only ever sees same-origin /sarvam-stt-ws.
//
// Sarvam's realtime API is STT-only -- unlike the old voice-server /call
// endpoint, it never generates or streams back a spoken reply. Every live
// call now answers through the app's own brain (FinGuru/Ollama) + TTS
// pipeline, same as turn mode -- see FinGuruChat's onTranscript handler.
//
// Always requests auto language detection ("auto") rather than hinting the
// currently-selected language, so each utterance's detected language
// (echoed back in transcript.final's `language` field) genuinely reflects
// what was heard -- the caller uses it to update the language picker and
// drive the reply's TTS language, same as turn mode.
function sarvamWsUrl() {
  return wsUrl('/sarvam-stt-ws', {
    language_code: 'auto',
    model: 'saaras:v3-realtime',
    sample_rate: '16000',
    encoding: 'linear16',
  });
}

// Sarvam recommends ~100ms audio chunks (3200 bytes / 1600 samples at 16kHz)
// per audio_input message, rather than the smaller frames the old raw-binary
// protocol used.
const SARVAM_FRAME_SAMPLES = 1600;

// Keep the realtime session alive during silence/mute (docs: WS closes with
// code 1008 after inactivity) -- a mic frame counts as activity too, so this
// is only load-bearing while muted.
const PING_INTERVAL_MS = 15000;

export const voiceCallSupported =
  typeof window !== 'undefined' &&
  !!navigator.mediaDevices?.getUserMedia &&
  !!(window.AudioContext || window.webkitAudioContext) &&
  typeof WebSocket !== 'undefined';

// Prompt 4 (network resilience) reconnect policy for the live-call WS.
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10000;
const RECONNECT_MAX_ATTEMPTS = 5;

function logEvent(msg, extra) {
  debugLog(`[VoiceCall][${new Date().toISOString()}] ${msg}`, extra ?? '');
}

function int16ToBase64(int16) {
  const bytes = new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

/**
 * Live voice call, streaming mic audio to Sarvam's realtime STT directly.
 * `onTranscript(text, language)` fires once per finalized utterance with
 * Sarvam's auto-detected language for that utterance; the caller is fully
 * responsible for generating and speaking a reply (there is no server-side
 * echo/reply here, unlike the old voice-server-backed call).
 */
export function useVoiceCall({ onTranscript, onError } = {}) {
  const [status, setStatus] = useState('idle'); // idle | connecting | live | reconnecting | ended
  const [muted, setMuted] = useState(false);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const mutedRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef(null);
  const pingTimerRef = useRef(null);
  const wsRef = useRef(null);
  const micCtxRef = useRef(null);
  const micStreamRef = useRef(null);
  const processorRef = useRef(null);
  const frameBufRef = useRef(new PCMFrameBuffer(SARVAM_FRAME_SAMPLES));
  const endedRef = useRef(false);
  const noRetryRef = useRef(false); // set on a fatal (e.g. auth) close -- don't reconnect
  const framesSentRef = useRef(0);
  const cbRef = useRef({ onTranscript, onError });
  cbRef.current = { onTranscript, onError };

  const cleanupAudio = useCallback(() => {
    try {
      processorRef.current?.disconnect();
    } catch {
      /* noop */
    }
    processorRef.current = null;
    try {
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
    } catch {
      /* noop */
    }
    micStreamRef.current = null;
    try {
      if (micCtxRef.current && micCtxRef.current.state !== 'closed') micCtxRef.current.close();
    } catch {
      /* noop */
    }
    micCtxRef.current = null;
  }, []);

  const stopPing = () => {
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  };

  const teardown = useCallback(
    (finalStatus) => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      stopPing();
      cleanupAudio();
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        try {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ event: 'end' }));
        } catch {
          /* noop */
        }
        try {
          ws.close(1000, 'client ended call');
        } catch {
          /* noop */
        }
      }
      setStatus(finalStatus);
    },
    [cleanupAudio]
  );

  const startMic = useCallback(() => {
    // Reuse the context created (and resumed) during the user gesture in
    // start(). A context created here, inside the async ws.onopen, can be born
    // 'suspended' -- its ScriptProcessor callback never fires, so no mic audio
    // is ever sent and Sarvam's VAD never triggers a turn.
    const ctx = micCtxRef.current;
    if (!ctx) return;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    logMicStart({ mode: 'live', sampleRate: ctx.sampleRate });
    logSttSent({ mode: 'live', provider: 'sarvam', streaming: true });
    debugLog('[VoiceCall] mic capture starting — ctx.state:', ctx.state, 'sampleRate:', ctx.sampleRate);
    const source = ctx.createMediaStreamSource(micStreamRef.current);
    const processor = ctx.createScriptProcessor(2048, 1, 1);
    processorRef.current = processor;
    processor.onaudioprocess = (e) => {
      // Read the CURRENT socket (not one captured at startMic-call time) so a
      // reconnect can swap the WS in without tearing down/recreating the mic
      // capture graph -- frames just resume flowing once the new WS is open.
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (mutedRef.current) {
        // Muted: drop the buffered frames instead of sending them, so no audio
        // is transmitted (not just silenced) and nothing stale gets flushed to
        // Sarvam the moment the user unmutes. The WS itself stays open.
        frameBufRef.current = new PCMFrameBuffer(SARVAM_FRAME_SAMPLES);
        return;
      }
      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = floatTo16kPCM16(input, ctx.sampleRate);
      const frames = frameBufRef.current.push(pcm16);
      for (const frame of frames) {
        ws.send(JSON.stringify({ event: 'audio_input', audio: int16ToBase64(frame) }));
      }
      framesSentRef.current += frames.length;
      if (framesSentRef.current < 3 || framesSentRef.current % 30 === 0) {
        let sum = 0;
        for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
        debugLog(
          '[VoiceCall] frames sent:',
          framesSentRef.current,
          'mic level(rms):',
          Math.sqrt(sum / input.length).toFixed(4),
          'ctx:',
          ctx.state
        );
      }
    };
    source.connect(processor);
    const silent = ctx.createGain();
    silent.gain.value = 0;
    processor.connect(silent);
    silent.connect(ctx.destination);
  }, []);

  const handleJson = useCallback((msg) => {
    switch (msg?.event) {
      case 'session.begin':
        debugLog('[VoiceCall] Sarvam session began — call is live', msg);
        setStatus('live');
        break;
      case 'transcript.final':
        logSttReceived({
          mode: 'live',
          provider: 'sarvam',
          detectedLanguage: msg.language,
          textLength: (msg.text || '').length,
          textPreview: (msg.text || '').slice(0, 120),
        });
        cbRef.current.onTranscript?.(msg.text, msg.language);
        break;
      case 'transcript.partial':
        // No partial-transcript UI in this app yet -- available via msg.text
        // if that changes.
        break;
      case 'vad.speech_start':
      case 'vad.speech_end':
      case 'config.updated':
      case 'pong':
        break;
      case 'error':
        logEvent('Sarvam realtime error', msg);
        if (msg.is_fatal) cbRef.current.onError?.('connection');
        break;
      case 'session.end':
        debugLog('[VoiceCall] Sarvam session ended', msg);
        endedRef.current = true;
        teardown('ended');
        break;
      default:
        break;
    }
  }, [teardown]);

  // Opens the Sarvam realtime WS and wires its handlers. Used for both the
  // initial connect and every reconnect attempt, so the wiring only exists
  // once. Deliberately does NOT touch the mic/AudioContext (those are set up
  // once in start() and survive reconnects) -- only the socket is replaced.
  const connectWs = useCallback(
    (isReconnect) => {
      let ws;
      try {
        ws = new WebSocket(sarvamWsUrl());
      } catch (e) {
        logEvent('WebSocket construction failed', { error: String(e) });
        scheduleReconnectRef.current();
        return;
      }
      wsRef.current = ws;
      frameBufRef.current = new PCMFrameBuffer(SARVAM_FRAME_SAMPLES); // discard any stale partial frame

      ws.onopen = () => {
        logEvent(isReconnect ? 'reconnected' : 'connected', { attempt: reconnectAttemptsRef.current });
        reconnectAttemptsRef.current = 0;
        setReconnectAttempt(0);
        stopPing();
        pingTimerRef.current = setInterval(() => {
          try {
            wsRef.current?.send(JSON.stringify({ event: 'ping' }));
          } catch {
            /* noop */
          }
        }, PING_INTERVAL_MS);
        if (!isReconnect) {
          // First connect: wire up mic capture. On reconnect the capture graph
          // already exists and just resumes sending once wsRef.current is OPEN.
          try {
            startMic();
          } catch (e) {
            logEvent('startMic failed', { error: String(e) });
          }
        }
      };
      ws.onmessage = (e) => {
        if (typeof e.data !== 'string') return; // Sarvam's realtime protocol is JSON-only (no binary frames)
        try {
          handleJson(JSON.parse(e.data));
        } catch {
          /* ignore non-JSON */
        }
      };
      ws.onclose = (event) => {
        if (endedRef.current) return; // user hung up -- teardown() already ran via end()
        wsRef.current = null;
        stopPing();
        logEvent('WS closed unexpectedly', { code: event.code, reason: event.reason });
        // 1003 = rate limit / quota exceeded / invalid key (Sarvam docs) --
        // retrying won't fix an auth/quota problem, so give up immediately
        // instead of burning the reconnect budget.
        if (event.code === 1003) {
          noRetryRef.current = true;
          cleanupAudio();
          setStatus('idle');
          cbRef.current.onError?.('auth');
          return;
        }
        scheduleReconnectRef.current();
      };
      ws.onerror = () => {
        /* onclose always follows in browsers; reconnect decision lives there */
      };
    },
    [handleJson, startMic, cleanupAudio]
  );

  // Exponential backoff: 500ms, 1s, 2s, 4s, 8s (capped at 10s), up to
  // RECONNECT_MAX_ATTEMPTS attempts, then give up and surface an error so the
  // caller can fall back (Prompt 7). Kept in a ref so connectWs's onclose
  // (defined above connectWs's own declaration order-wise) can call the
  // latest version without a circular useCallback dependency.
  const scheduleReconnectRef = useRef(() => {});
  scheduleReconnectRef.current = () => {
    if (endedRef.current || noRetryRef.current) return;
    // Mic/AudioContext deliberately stay alive across reconnect attempts --
    // only cleaned up once we actually give up (below).
    const attempt = reconnectAttemptsRef.current;
    if (attempt >= RECONNECT_MAX_ATTEMPTS) {
      logEvent('reconnect attempts exhausted -- giving up', { attempts: attempt });
      cleanupAudio();
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      setStatus('idle');
      cbRef.current.onError?.('reconnect_exhausted');
      return;
    }
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
    reconnectAttemptsRef.current = attempt + 1;
    setReconnectAttempt(attempt + 1);
    setStatus('reconnecting');
    logEvent(`scheduling reconnect attempt ${attempt + 1}/${RECONNECT_MAX_ATTEMPTS} in ${delay}ms`);
    reconnectTimerRef.current = setTimeout(() => {
      if (endedRef.current) return;
      connectWs(true);
    }, delay);
  };

  const start = useCallback(async () => {
    if (!voiceCallSupported) {
      cbRef.current.onError?.('unsupported');
      return;
    }
    endedRef.current = false;
    noRetryRef.current = false;
    reconnectAttemptsRef.current = 0;
    setReconnectAttempt(0);
    framesSentRef.current = 0;
    mutedRef.current = false; // each new call starts unmuted
    setMuted(false);
    setStatus('connecting');
    logEvent('start() — connecting', { url: sarvamWsUrl() });

    // Create + resume the mic AudioContext NOW, synchronously in the click
    // gesture, so it's 'running' by the time startMic() wires up capture.
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const micCtx = new AudioCtx();
    micCtxRef.current = micCtx;
    try {
      await micCtx.resume();
    } catch {
      /* best effort */
    }
    logEvent('mic AudioContext state after resume', { state: micCtx.state });

    try {
      micStreamRef.current = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      logEvent(
        'mic granted',
        micStreamRef.current.getAudioTracks().map((t) => `${t.label} (enabled=${t.enabled}, muted=${t.muted})`)
      );
    } catch {
      cleanupAudio();
      setStatus('idle');
      cbRef.current.onError?.('mic_denied');
      return;
    }

    connectWs(false);
  }, [cleanupAudio, connectWs]);

  const end = useCallback(() => {
    endedRef.current = true;
    teardown('ended');
  }, [teardown]);

  // Toggles outgoing mic transmission only -- the WS connection stays open,
  // so unmuting has zero reconnect latency.
  const toggleMute = useCallback(() => {
    mutedRef.current = !mutedRef.current;
    setMuted(mutedRef.current);
    debugLog('[VoiceCall] mic', mutedRef.current ? 'muted' : 'unmuted');
  }, []);

  // Explicit set, for the caller (auto-mute-while-thinking/speaking) rather
  // than the user's own "M" toggle -- same underlying mechanism as
  // toggleMute, just not a flip. A no-op if already in the requested state,
  // so a caller can call this every render without spamming the debug log.
  const setMicMuted = useCallback((next) => {
    if (mutedRef.current === next) return;
    mutedRef.current = next;
    setMuted(next);
    debugLog('[VoiceCall] mic', next ? 'auto-muted' : 'auto-unmuted');
  }, []);

  return {
    supported: voiceCallSupported,
    status, // idle | connecting | live | reconnecting | ended
    reconnectAttempt, // 0 when not reconnecting, else the current attempt number
    reconnectMaxAttempts: RECONNECT_MAX_ATTEMPTS,
    muted,
    toggleMute,
    setMicMuted,
    start,
    end,
  };
}

export default useVoiceCall;
