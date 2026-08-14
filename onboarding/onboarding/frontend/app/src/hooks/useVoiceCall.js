import { useCallback, useRef, useState } from 'react';
import { PCMFrameBuffer, ReplyAudioAssembler, floatTo16kPCM16 } from '../lib/liveCall';
import { logMicStart, logSttSent, logSttReceived, logTtsReceived, debugLog } from '../lib/pipelineLog';

// Same-origin WS that the Vite dev proxy forwards to <voice-server>/voice/call
// (with the token attached server-side). Mirrors the reference live_call.py
// protocol: mic -> 16kHz PCM16 frames out; JSON control + PCM16 reply in.
// The selected language rides along on the handshake query string; the Vite
// proxy forwards it to /voice/call?token=...&lang=..., so it's set before any
// audio flows. The server's param name is `lang` (en/ta/hi/te/...). A server
// that doesn't read it simply ignores it.
function voiceWsUrl(language) {
  const u = new URL('/voice-ws', window.location.href);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  if (language) u.searchParams.set('lang', language);
  return u.toString();
}

export const voiceCallSupported =
  typeof window !== 'undefined' &&
  !!navigator.mediaDevices?.getUserMedia &&
  !!(window.AudioContext || window.webkitAudioContext) &&
  typeof WebSocket !== 'undefined';

// Prompt 4 (network resilience) reconnect policy for the live-call WS.
// NOTE: the reference voice server exposes ONE combined WS (/call) for
// STT + TTS + VAD together, not independent STT/TTS/LLM sockets -- there is
// no backend capability for three separately-reconnectable connections here,
// so this reconnects the single real connection this app has.
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10000;
const RECONNECT_MAX_ATTEMPTS = 5;

function logEvent(msg, extra) {
  debugLog(`[VoiceCall][${new Date().toISOString()}] ${msg}`, extra ?? '');
}

/**
 * Live full-duplex voice call to the reference voice server.
 * `onTranscript(text)` fires for recognized user speech, `onReplyText(text)`
 * for the server's spoken reply text. Reply audio is played automatically
 * unless `playServerReplies` is false -- set that when you want to generate
 * the reply yourself (e.g. route the transcript through FinGuru) instead of
 * playing the server's built-in response hook.
 */
export function useVoiceCall({
  onTranscript,
  onReplyText,
  onError,
  playServerReplies = true,
  onSpeakingChange, // (bool) -- true while the server's reply audio is actually playing
  language, // selected language code (e.g. 'ta'); sent on the WS handshake
} = {}) {
  const [status, setStatus] = useState('idle'); // idle | connecting | live | reconnecting | ended
  const [lastLatencyMs, setLastLatencyMs] = useState(null);
  const [muted, setMuted] = useState(false);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const mutedRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef(null);
  const wsRef = useRef(null);
  const micCtxRef = useRef(null);
  const micStreamRef = useRef(null);
  const processorRef = useRef(null);
  const frameBufRef = useRef(new PCMFrameBuffer());
  const assemblerRef = useRef(new ReplyAudioAssembler());
  const playCtxRef = useRef(null);
  const nextPlayRef = useRef(0);
  const currentSourceRef = useRef(null); // the AudioBufferSourceNode currently playing a reply, if any
  const endedRef = useRef(false);
  const framesSentRef = useRef(0);
  // Kept in a ref so connect/reconnect always read the CURRENT selection
  // (including a mid-session dropdown or STT-auto-detect change) without the
  // WS-wiring callbacks needing `language` in their dependency arrays.
  const languageRef = useRef(language);
  languageRef.current = language;
  const cbRef = useRef({ onTranscript, onReplyText, onError, playServerReplies, onSpeakingChange });
  cbRef.current = { onTranscript, onReplyText, onError, playServerReplies, onSpeakingChange };

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

  const teardown = useCallback(
    (finalStatus) => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      cleanupAudio();
      try {
        if (playCtxRef.current && playCtxRef.current.state !== 'closed') playCtxRef.current.close();
      } catch {
        /* noop */
      }
      playCtxRef.current = null;
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
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

  const playReply = useCallback((sampleRate, int16) => {
    if (!sampleRate || int16.length === 0) return;
    // Server's built-in TTS reply arriving as one merged buffer (live mode,
    // playServerReplies path) -- stage 7, once per turn.
    logTtsReceived({ mode: 'live', sampleRate, samples: int16.length });
    if (!playCtxRef.current) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      playCtxRef.current = new Ctx();
      nextPlayRef.current = playCtxRef.current.currentTime;
    }
    const ctx = playCtxRef.current;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    const f32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / (int16[i] < 0 ? 0x8000 : 0x7fff);
    const buffer = ctx.createBuffer(1, f32.length, sampleRate);
    buffer.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, nextPlayRef.current);
    // The assembler hands us one whole merged reply as a single buffer (see
    // ReplyAudioAssembler.handleJson), so a single onended cleanly marks
    // "done speaking" -- no separate per-chunk tracking needed.
    cbRef.current.onSpeakingChange?.(true);
    currentSourceRef.current = src;
    src.onended = () => {
      if (currentSourceRef.current === src) currentSourceRef.current = null;
      cbRef.current.onSpeakingChange?.(false);
    };
    src.start(startAt);
    nextPlayRef.current = startAt + buffer.duration;
  }, []);

  // Prompt 6 (barge-in): hard-stop the server's reply audio the instant the
  // user starts talking again. `stop()` on an already-scheduled/playing
  // source fires `onended` synchronously-ish, which clears onSpeakingChange.
  const stopSpeaking = useCallback(() => {
    const src = currentSourceRef.current;
    if (!src) return;
    try {
      src.stop();
    } catch {
      /* already stopped/ended */
    }
    currentSourceRef.current = null;
    if (playCtxRef.current) nextPlayRef.current = playCtxRef.current.currentTime;
    cbRef.current.onSpeakingChange?.(false);
  }, []);

  const startMic = useCallback(() => {
    // Reuse the context created (and resumed) during the user gesture in
    // start(). A context created here, inside the async ws.onopen, can be born
    // 'suspended' -- its ScriptProcessor callback never fires, so no mic audio
    // is ever sent and the server's VAD never triggers a turn.
    const ctx = micCtxRef.current;
    if (!ctx) return;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    // Live mode: mic capture begins here and PCM frames stream continuously to
    // the server, which runs STT on them (VAD-gated) -- so this single point is
    // both "mic started" (1) and "audio streaming to STT" (2) for this call.
    logMicStart({ mode: 'live', sampleRate: ctx.sampleRate });
    logSttSent({ mode: 'live', streaming: true });
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
        // the server the moment the user unmutes. The WS itself stays open.
        frameBufRef.current = new PCMFrameBuffer();
        return;
      }
      const input = e.inputBuffer.getChannelData(0);
      const pcm16 = floatTo16kPCM16(input, ctx.sampleRate);
      const frames = frameBufRef.current.push(pcm16);
      for (const frame of frames) ws.send(frame.buffer);
      framesSentRef.current += frames.length;
      // Log the first couple of sends and then periodically, with mic level so
      // we can tell "not capturing" (rms~0) from "capturing but no server turn".
      if (framesSentRef.current < 3 || framesSentRef.current % 150 === 0) {
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

  const handleJson = useCallback(
    (msg) => {
      switch (msg.type) {
        case 'ready':
          debugLog('[VoiceCall] server ready — call is live', msg);
          setStatus('live');
          break;
        case 'transcript':
          logSttReceived({
            mode: 'live',
            detectedLanguage: msg.detected_language ?? msg.language,
            textLength: (msg.text || '').length,
            textPreview: (msg.text || '').slice(0, 120),
          });
          // Forward the STT-detected language (if the server reports one) so the
          // caller can auto-match the reply language.
          cbRef.current.onTranscript?.(msg.text, msg.detected_language ?? msg.language);
          break;
        case 'reply_text':
          debugLog('[VoiceCall] reply_text from server:', msg.text);
          cbRef.current.onReplyText?.(msg.text);
          break;
        case 'reply_audio_start':
        case 'reply_audio_end': {
          const res = assemblerRef.current.handleJson(msg);
          // Skip the server's built-in reply audio when the caller supplies
          // its own reply pipeline (e.g. FinGuru); otherwise it just echoes.
          if (res && cbRef.current.playServerReplies !== false) playReply(res.samplingRate, res.samples);
          break;
        }
        case 'latency':
          setLastLatencyMs(msg.round_trip_ms ?? null);
          debugLog('[Voice] live latency', {
            sttMs: msg.stt_ms != null ? Math.round(msg.stt_ms) : null,
            ttsFirstChunkMs: msg.tts_first_chunk_ms != null ? Math.round(msg.tts_first_chunk_ms) : null,
            roundTripMs: msg.round_trip_ms != null ? Math.round(msg.round_trip_ms) : null,
          });
          break;
        case 'call_ended':
          endedRef.current = true;
          teardown('ended');
          break;
        default:
          break;
      }
    },
    [playReply, teardown]
  );

  // Opens the live-call WS and wires its handlers. Used for both the initial
  // connect and every reconnect attempt, so the wiring only exists once.
  // Deliberately does NOT touch the mic/AudioContext (those are set up once
  // in start() and survive reconnects) -- only the socket is replaced.
  const connectWs = useCallback(
    (isReconnect) => {
      let ws;
      try {
        ws = new WebSocket(voiceWsUrl(languageRef.current));
        ws.binaryType = 'arraybuffer';
      } catch (e) {
        logEvent('WebSocket construction failed', { error: String(e) });
        scheduleReconnectRef.current();
        return;
      }
      wsRef.current = ws;
      frameBufRef.current = new PCMFrameBuffer(); // discard any stale partial frame
      assemblerRef.current = new ReplyAudioAssembler(); // a reply mid-flight can't be resumed

      ws.onopen = () => {
        logEvent(isReconnect ? 'reconnected' : 'connected', { attempt: reconnectAttemptsRef.current });
        reconnectAttemptsRef.current = 0;
        setReconnectAttempt(0);
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
        if (typeof e.data === 'string') {
          try {
            handleJson(JSON.parse(e.data));
          } catch {
            /* ignore non-JSON */
          }
        } else if (e.data instanceof ArrayBuffer) {
          assemblerRef.current.handleBinary(e.data);
        }
      };
      ws.onclose = (event) => {
        if (endedRef.current) return; // user hung up -- teardown() already ran via end()
        wsRef.current = null;
        logEvent('WS closed unexpectedly', { code: event.code, reason: event.reason });
        scheduleReconnectRef.current();
      };
      ws.onerror = () => {
        /* onclose always follows in browsers; reconnect decision lives there */
      };
    },
    [handleJson, startMic]
  );

  // Exponential backoff: 500ms, 1s, 2s, 4s, 8s (capped at 10s), up to
  // RECONNECT_MAX_ATTEMPTS attempts, then give up and surface an error so the
  // caller can fall back (Prompt 7). Kept in a ref so connectWs's onclose
  // (defined above connectWs's own declaration order-wise) can call the
  // latest version without a circular useCallback dependency.
  const scheduleReconnectRef = useRef(() => {});
  scheduleReconnectRef.current = () => {
    if (endedRef.current) return;
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
    reconnectAttemptsRef.current = 0;
    setReconnectAttempt(0);
    framesSentRef.current = 0;
    mutedRef.current = false; // each new call starts unmuted
    setMuted(false);
    setStatus('connecting');
    logEvent('start() — connecting', { url: voiceWsUrl(languageRef.current) });

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

  // Toggles outgoing mic transmission only -- the WS connection and any
  // in-progress reply playback are untouched, so unmuting has zero
  // reconnect latency and muting never interrupts audio you're hearing.
  const toggleMute = useCallback(() => {
    mutedRef.current = !mutedRef.current;
    setMuted(mutedRef.current);
    debugLog('[VoiceCall] mic', mutedRef.current ? 'muted' : 'unmuted');
  }, []);

  return {
    supported: voiceCallSupported,
    status, // idle | connecting | live | reconnecting | ended
    reconnectAttempt, // 0 when not reconnecting, else the current attempt number
    reconnectMaxAttempts: RECONNECT_MAX_ATTEMPTS,
    lastLatencyMs,
    muted,
    toggleMute,
    stopSpeaking, // Prompt 6: hard-stop server reply audio for barge-in
    start,
    end,
  };
}

export default useVoiceCall;
