import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import PhoneScreen from '../components/PhoneScreen';
import { BotBubble, TypingBubble, UserBubble } from '../components/ChatBubble';
import LevelMeter from '../components/LevelMeter';
import MicIcon from '../components/MicIcon';
import VoiceMessageBubble from '../components/VoiceMessageBubble';
import { askFinGuru } from '../api/finguru';
import { askFinGuruStreaming } from '../api/finguruStream';
import { askOllama, OLLAMA_ENABLED, USE_OLLAMA_IN_VOICE, LLM_STREAMING_ENABLED } from '../api/ollama';
import { streamOllamaToTTS } from '../api/ollamaStream';
import { useVoiceRecorder } from '../lib/voiceRecorder';
import { transcribeAudio, synthesizeText, playAudioBlob, stopAudio, checkVoiceHealth } from '../api/voice';
import { VOICE_MODE, SKIP_FINGURU_IN_VOICE, BARGE_IN_ENABLED, BARGE_IN_MIN_CHARS } from '../lib/voiceConfig';
import { useVoiceCall } from '../hooks/useVoiceCall';
import { LANGUAGE_NAMES, ENABLED_LANGUAGES, DEFAULT_LANGUAGE, resolveLanguageCode } from '../lib/languages';
import { getProfileId } from '../lib/finguruProfile';
import { getConversation, upsertConversation, trimRetention, historySupported, MAX_CONVERSATIONS } from '../lib/historyDb';
import NamePrompt from '../components/NamePrompt';
import ToolCard from '../components/ToolCard';
import { useFinGuruName } from '../lib/finguruIdentity';
import { fetchNameHistory } from '../api/finguruHistory';
import { logMicStart, debugLog } from '../lib/pipelineLog';
import { needsSarvamTts } from '../lib/ttsRouter';
import { withFollowUpInstruction, extractFollowUps } from '../lib/followUps';

const LAST_CONVERSATION_KEY = 'finguru.lastConversationId';
const FOLLOW_UPS_KEY = 'finguru.followUpsEnabled';

function newMsgId() {
  return (typeof crypto !== 'undefined' && crypto.randomUUID && crypto.randomUUID()) || `m-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// Badge text confirming which provider handles/handled a given STT/TTS call.
function providerLabel(provider) {
  if (provider === 'sarvam') return 'Sarvam';
  if (provider === 'local') return 'Local';
  return 'Unknown';
}

// TTS provider is only known FOR SURE once a reply has actually been spoken
// (m.ttsProvider, set by speakMessage()/processVoiceJob()) -- before that,
// predict it with the same client-side router synthesizeText() itself uses
// (lib/ttsRouter.js), so the badge appears immediately under every reply
// instead of only after the user hits Play.
function predictedTtsProvider(text) {
  return needsSarvamTts(text) ? 'sarvam' : 'local';
}

// Ollama is the live-mode voice brain (instead of FinGuru) when both the
// skip-FinGuru and use-Ollama-in-voice flags are on (and Ollama is enabled).
// When VITE_VOICE_SKIP_FINGURU is false this is false and the FinGuru path runs.
const OLLAMA_FALLBACK_ACTIVE = SKIP_FINGURU_IN_VOICE && USE_OLLAMA_IN_VOICE && OLLAMA_ENABLED;

// Whether that Ollama reply is STREAMED sentence-by-sentence to TTS or
// answered the existing ONE-SHOT way (full response -> one /synthesize call)
// is a separate, independent choice, gated purely by LLM_STREAMING_ENABLED --
// default false leaves the one-shot flow completely untouched.
const STREAMING_OLLAMA_ACTIVE = OLLAMA_FALLBACK_ACTIVE && LLM_STREAMING_ENABLED;

// --- minimal markdown rendering for the agent's answer text ---
// The agent returns light markdown (**bold**, "*" bullets, blank-line paras).
function renderInline(text) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    /^\*\*[^*]+\*\*$/.test(part) ? <strong key={i}>{part.slice(2, -2)}</strong> : <span key={i}>{part}</span>
  );
}

function Markdown({ text }) {
  const lines = (text || '').split('\n');
  return (
    <div className="flex flex-col gap-1">
      {lines.map((line, i) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={i} className="h-1.5" />;
        const isBullet = /^[*-]\s+/.test(trimmed);
        const stripped = trimmed.replace(/^[*-]\s+/, '').replace(/^#{1,6}\s+/, '');
        const isHeading = /^#{1,6}\s+/.test(trimmed);
        return (
          <p key={i} className={`${isBullet ? 'relative pl-4' : ''} ${isHeading ? 'font-bold' : ''}`}>
            {isBullet && <span className="absolute left-0">•</span>}
            {renderInline(stripped)}
          </p>
        );
      })}
    </div>
  );
}

export default function FinGuruChat() {
  const location = useLocation();
  const seed = location.state?.seed || null;

  // Name-based session identity (see lib/finguruIdentity.js) -- the `name` IS
  // the session key sent on every backend request; called unconditionally
  // (rules-of-hooks) even before it resolves, gated only at the JSX return.
  const { name, setName } = useFinGuruName();
  const nameRef = useRef(null);
  useEffect(() => {
    nameRef.current = name;
  }, [name]);
  const [nameHistoryChecked, setNameHistoryChecked] = useState(false); // backend /api/history fetch settled (success or soft-fail)
  const [nameHistory, setNameHistory] = useState(null); // authoritative per-fetch: overwritten wholesale, never merged

  // Per the name-identity spec: on load with a resolved name, check the
  // backend's history for that name before treating the chat as ready.
  // ADDITIVE per explicit product decision: this does NOT replace or merge
  // into the existing local (IndexedDB, Prompt 3) conversation -- it's a
  // separate, clearly-labeled layer shown above the chat (see render below).
  // That backend isn't deployed yet, so this call is expected to soft-fail;
  // the "before allowing a new message" gate is intentionally brief (a few
  // seconds, not indefinite) so a permanently-missing backend never blocks
  // the existing chat from working.
  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    setNameHistoryChecked(false);
    (async () => {
      const data = await fetchNameHistory(name);
      if (cancelled) return;
      setNameHistory(data && Array.isArray(data.messages) ? data.messages : null);
      setNameHistoryChecked(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [name]);

  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [colloquial, setColloquial] = useState(false);
  // Recommended follow-up questions -- text mode only (see send()).
  //
  // On unless the user has turned it off, and their choice survives a reload.
  // Two separate things were wrong before: it defaulted off, which made
  // "follow-ups aren't appearing" the expected behaviour rather than a bug,
  // and the state was not persisted, so switching it off lasted only until
  // the next refresh. A toggle that forgets is worse than no toggle.
  //
  // Read as "anything but the string 'false' means on", so a first-time user
  // (no key) and a user who switched it on both land on -- only an explicit
  // off is remembered as off.
  const [followUpsEnabled, setFollowUpsEnabled] = useState(() => {
    try {
      return localStorage.getItem(FOLLOW_UPS_KEY) !== 'false';
    } catch {
      return true;                      // private mode / storage disabled
    }
  });
  const [language, setLanguage] = useState(DEFAULT_LANGUAGE); // response + voice language code
  const [voiceStatus, setVoiceStatus] = useState('idle'); // idle | transcribing | speaking
  // Track by stable message id (not array index) -- an index captured from
  // inside a setMessages() updater's side effect isn't reliably correct
  // (React may invoke updaters more than once, notably under StrictMode),
  // so every "which message is this about" reference in this file uses the
  // message's `id` (set once, up front, before the message is ever pushed).
  const [speakingId, setSpeakingId] = useState(null); // message currently playing
  const [preparingId, setPreparingId] = useState(null); // message whose audio is being synthesized
  const [voiceNote, setVoiceNote] = useState(null); // transient error/status line
  const speakReqRef = useRef(0); // invalidates superseded/cancelled synth requests
  const bottomRef = useRef(null);
  const messagesRef = useRef([]);
  const colloquialRef = useRef(false);
  const languageRef = useRef(DEFAULT_LANGUAGE);
  const voiceBusyRef = useRef(false); // true while asking/synthesizing/speaking
  const seeded = useRef(false);

  // --- Prompt 2: record-then-send voice messages (turn mode) ---
  // recordingPhase: 'idle' | 'recording' | 'preview'
  const [recordingPhase, setRecordingPhase] = useState('idle');
  const [recordingLevel, setRecordingLevel] = useState(0);
  const [recordingElapsed, setRecordingElapsed] = useState(0);
  const [previewDuration, setPreviewDuration] = useState(0);
  const previewBlobRef = useRef(null);
  const previewUrlRef = useRef(null);
  const recordingStartRef = useRef(0);
  const recordingTimerRef = useRef(null);
  const voiceQueueRef = useRef([]); // pending {id, blob} jobs, FIFO
  const queueRunningRef = useRef(false);
  const [queueDepth, setQueueDepth] = useState(0); // messages waiting behind the one in flight

  // --- Prompt 3: persistent chat history (IndexedDB) ---
  const conversationIdRef = useRef(null);
  const conversationMetaRef = useRef({ startedAt: null, title: null });
  const [historyReady, setHistoryReady] = useState(false); // hydration decided -> safe to persist

  // --- Prompt 5: pipeline-stage feedback (live mode's Thinking/Speaking,
  // which aren't otherwise observable -- turn mode reuses existing per-message
  // status text instead of duplicating state, see the render section below). ---
  const [ollamaThinking, setOllamaThinking] = useState(false); // Ollama-fallback: request in flight, before first spoken sentence
  const [liveSpeaking, setLiveSpeaking] = useState(false); // FinGuru/Ollama-TTS reply audio currently playing

  // --- Prompt 6: barge-in bookkeeping (live mode only) ---
  const ollamaAbortRef = useRef(null); // AbortController for the in-flight streaming-Ollama turn, if any
  const ollamaMsgIdRef = useRef(null); // id of the outbound message the streaming-Ollama turn is writing to

  // --- Prompt 7: text fallback when the voice pipeline fails ---
  // null = voice presumed healthy. Otherwise:
  //   { reason: 'connection'|'stt'|'llm'|'tts'|'manual', message, ttsOk }
  // ttsOk = whether replies to fallback-typed messages should still be spoken
  // (true unless TTS itself is the thing that's down).
  const [voiceFallback, setVoiceFallback] = useState(null);
  const enterFallback = (reason, message, ttsOk = true) => setVoiceFallback({ reason, message, ttsOk });
  const clearFallbackIfReason = (reason) =>
    setVoiceFallback((prev) => (prev && prev.reason === reason ? null : prev));

  // Background auto-retry: only for reasons we can cheaply verify recovery of
  // (the voice server's own /health). LLM failures (FinGuru/Ollama) have no
  // equivalent cheap probe available client-side, so those only clear via the
  // manual "Try voice again" button or by a subsequent call actually succeeding.
  useEffect(() => {
    if (!voiceFallback || !['connection', 'stt', 'tts'].includes(voiceFallback.reason)) return;
    const interval = setInterval(async () => {
      const healthy = await checkVoiceHealth();
      if (healthy) {
        debugLog('[FinGuru] voice pipeline recovered (background health check)');
        setVoiceFallback(null);
      }
    }, 20000);
    return () => clearInterval(interval);
  }, [voiceFallback]);

  // keep a ref so async handlers read the current language selection
  useEffect(() => {
    languageRef.current = language;
  }, [language]);

  const recorder = useVoiceRecorder((rms) => setRecordingLevel(rms));

  // Ollama-fallback streaming path (VITE_VOICE_SKIP_FINGURU && VITE_USE_OLLAMA_IN_VOICE):
  // stream Ollama tokens and forward each complete sentence to the TTS WS as
  // soon as it's ready, so audio starts before the full answer is generated.
  const runStreamingOllama = async (text) => {
    const outboundId = newMsgId(); // generated up front -- no index-capture needed anywhere below
    setMessages((prev) => [
      ...prev,
      { id: newMsgId(), direction: 'inbound', text, sttProvider: 'sarvam' },
      { id: outboundId, direction: 'outbound', text: '…' },
    ]);
    ollamaMsgIdRef.current = outboundId; // Prompt 6: lets a barge-in mark this exact message interrupted
    const controller = new AbortController();
    ollamaAbortRef.current = controller;
    let acc = '';
    // The LLM token stream finishes well BEFORE the streamed TTS audio does.
    // Keep the abort handle (ollamaAbortRef) valid until the AUDIO ends -- not
    // just until this promise resolves -- so the Stop button can still tear the
    // stream down during playback. Release only once audio has drained (tracked
    // via onSpeaking) or immediately if no audio ever played.
    let streamDone = false;
    let speakingNow = false;
    const releaseIfIdle = () => {
      // Guard: a barge-in may have already swapped in a newer turn's controller;
      // only release if we're still the current one.
      if (ollamaAbortRef.current === controller) {
        ollamaAbortRef.current = null;
        voiceBusyRef.current = false;
      }
    };
    setOllamaThinking(true);
    try {
      const result = await streamOllamaToTTS(text, {
        language: languageRef.current,
        colloquial: colloquialRef.current,
        signal: controller.signal,
        name: nameRef.current,
        onToken: (tok) => {
          acc += tok;
          updateMessageById(outboundId, { text: acc });
        },
        onSpeaking: (speaking) => {
          speakingNow = speaking;
          setLiveSpeaking(speaking);
          setSpeakingId(speaking ? outboundId : null);
          if (speaking) setOllamaThinking(false); // audio started -> no longer "thinking"
          else if (streamDone) releaseIfIdle(); // audio drained after the stream ended -> release
        },
      });
      if (result?.interrupted) {
        // Barge-in already marked the message + started the new turn; nothing more to do.
        return;
      }
      clearFallbackIfReason('llm'); // a streaming-Ollama reply succeeded -> LLM is fine
      if (result?.ttsFallbackNeeded) {
        // /tts/stream never connected this turn (e.g. Prompt 3's
        // TTS_STREAMING_ENABLED is off on the GPU PC) -- the text is already
        // shown via onToken; speak it now through the existing non-streaming
        // REST /synthesize + play, exactly like the one-shot Ollama path does.
        // Mark "Preparing" on the message while that (slow, ~15s) REST call
        // is in flight -- this was previously missing, so the Play/Preparing
        // button on this message just sat on "Play" the whole time.
        setPreparingId(outboundId);
        try {
          const { blob, provider } = await synthesizeText(result.text || acc, languageRef.current);
          setPreparingId(null);
          setSpeakingId(outboundId);
          setLiveSpeaking(true);
          updateMessageById(outboundId, { ttsProvider: provider });
          playAudioBlob(blob, {
            onEnd: () => {
              setSpeakingId(null);
              setLiveSpeaking(false);
            },
          });
        } catch {
          setPreparingId(null);
          setLiveSpeaking(false);
          setVoiceNote('Voice reply unavailable — showing text instead.');
        }
      }
    } catch (err) {
      // Prompt 7: the streaming-Ollama LLM (voice's configured brain here)
      // failed -- surface the text fallback. err.midStream (see
      // ollamaStream.js) distinguishes "never started" from "dropped
      // partway"; either way the user needs a way to keep going.
      enterFallback(
        'llm',
        err?.midStream
          ? 'The voice assistant lost its train of thought — you can keep typing.'
          : 'The voice assistant is temporarily unavailable — you can type instead.',
        true
      );
      updateMessageById(outboundId, { text: 'Voice server is unavailable right now.', variant: 'error' });
    } finally {
      setOllamaThinking(false);
      streamDone = true;
      // If audio is still playing, the onSpeaking(false) handler above releases
      // the abort handle once it finishes; otherwise (no audio this turn, an
      // error, or audio already drained) release now. releaseIfIdle's guard
      // leaves a barge-in's newer controller untouched.
      if (!speakingNow) releaseIfIdle();
    }
  };

  // Live-call voice mode (VITE_VOICE_MODE=live): full-duplex mic stream to
  // Sarvam's realtime STT directly (see hooks/useVoiceCall.js), then run the
  // transcript through FinGuru/Ollama (one-shot or streaming) and speak the
  // answer via synthesizeText (local or Sarvam, per lib/ttsRouter.js) --
  // exactly like turn mode. Sarvam's realtime API is STT-only, so there is
  // no server-side reply/echo to suppress or play anymore.
  const voiceCall = useVoiceCall({
    onTranscript: (text, detectedLanguage) => {
      const t = text?.trim();
      if (!t) return;
      clearFallbackIfReason('connection'); // a transcript arriving proves the call is actually working again
      // Match the reply language to whatever STT heard (updates the picker +
      // languageRef before the brain/TTS calls below run).
      if (applyDetectedLanguage(detectedLanguage) === 'unsupported') {
        setMessages((prev) => [
          ...prev,
          { id: newMsgId(), direction: 'inbound', text: t, sttProvider: 'sarvam' },
          { id: newMsgId(), direction: 'outbound', text: UNSUPPORTED_LANGUAGE_MESSAGE, variant: 'error' },
        ]);
        return;
      }
      // Prompt 6 (barge-in): a transcript arriving while the assistant is
      // already SPEAKING can interrupt it, if enabled and long enough to be
      // a deliberate interruption rather than noise. During "thinking"
      // (brain call in flight, nothing playing yet) we still just drop it --
      // there's nothing to barge in on, and starting a second brain call in
      // parallel would race.
      const canBargeIn = BARGE_IN_ENABLED && t.length >= BARGE_IN_MIN_CHARS;

      if (SKIP_FINGURU_IN_VOICE && !OLLAMA_FALLBACK_ACTIVE) {
        // Neither FinGuru nor Ollama is configured to answer voice questions
        // -- there's no brain to generate a reply with, so just show what
        // was heard (there's no server-side echo to fall back to anymore).
        setMessages((prev) => [...prev, { id: newMsgId(), direction: 'inbound', text: t, sttProvider: 'sarvam' }]);
        return;
      }

      if (STREAMING_OLLAMA_ACTIVE) {
        // Stream Ollama tokens -> sentence-level TTS (LLM_STREAMING_ENABLED=true).
        if (!voiceBusyRef.current) {
          voiceBusyRef.current = true;
          runStreamingOllama(t);
          return;
        }
        if (canBargeIn && liveSpeaking) {
          updateMessageById(ollamaMsgIdRef.current, { interrupted: true });
          stopSpeakingNow();
          voiceBusyRef.current = true;
          runStreamingOllama(t);
        }
        return;
      }

      // One-shot brain (FinGuru, or Ollama when LLM_STREAMING_ENABLED=false)
      // -- the existing, unmodified flow: full response, one /synthesize call.
      // Ignore transcripts while we're already answering/speaking -- this also
      // stops our own spoken reply (re-heard by the mic) from looping back.
      if (!voiceBusyRef.current) {
        voiceBusyRef.current = true; // guard immediately; the effect keeps it in sync after
        send(t, { speak: true, sttProvider: 'sarvam' });
        return;
      }
      if (canBargeIn && speakingId !== null) {
        updateMessageById(speakingId, { interrupted: true });
        stopSpeakingNow();
        voiceBusyRef.current = true;
        send(t, { speak: true, sttProvider: 'sarvam' });
      }
    },
    onError: (reason) => {
      setVoiceNote(
        reason === 'mic_denied'
          ? 'Microphone permission denied.'
          : reason === 'unsupported'
            ? 'Live voice calls are not supported in this browser.'
            : reason === 'auth'
              ? 'Voice call rejected (invalid or over-quota Sarvam key) — you can type instead.'
              : reason === 'reconnect_exhausted'
                ? "Couldn't reconnect the voice call after several tries — you can type instead, or tap 📞 to start a new call."
                : 'Voice call connection was lost.'
      );
      // Prompt 7: reconnection exhausted (Prompt 4), an auth/quota rejection,
      // or the socket never opened at all -- surface the text fallback
      // rather than leaving the user stuck in a dead call.
      if (reason === 'reconnect_exhausted' || reason === 'ws_failed' || reason === 'auth') {
        enterFallback('connection', "Voice call isn't available right now — you can type instead.", false);
      }
    },
  });
  // 'reconnecting' stays "in call" -- the call isn't over, the connection is
  // just being re-established; tearing the UI down here would be more
  // disruptive than the brief automatic reconnect itself.
  const inCall = ['connecting', 'live', 'reconnecting'].includes(voiceCall.status);

  // `voiceCall` is a new object every render, so effects read it via this ref
  // instead of depending on it directly (its identity changing would re-fire
  // effects every render / trigger exhaustive-deps false positives).
  const voiceCallRef = useRef(voiceCall);
  voiceCallRef.current = voiceCall;

  // "M" toggles mic mute during an active call -- ignored while the user is
  // typing (input/textarea/select/contenteditable) so it never hijacks text entry.
  // Uses VOICE_MODE (module-level) rather than the later-declared `liveMode`
  // local to avoid a temporal-dead-zone reference here.
  useEffect(() => {
    if (VOICE_MODE !== 'live' || !inCall) return;
    const onKeyDown = (e) => {
      if (e.key !== 'm' && e.key !== 'M') return;
      const tag = e.target?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target?.isContentEditable) return;
      e.preventDefault();
      voiceCallRef.current?.toggleMute();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [inCall]);

  // keep a ref so the seed-effect's send() reads the current toggle value
  useEffect(() => {
    colloquialRef.current = colloquial;
  }, [colloquial]);

  // Remember the follow-ups choice across reloads. Written on every change
  // rather than only on the off transition, so switching back on clears a
  // previously stored off.
  useEffect(() => {
    try {
      localStorage.setItem(FOLLOW_UPS_KEY, String(followUpsEnabled));
    } catch {
      /* storage unavailable -- the toggle still works for this session */
    }
  }, [followUpsEnabled]);

  // keep a ref of messages so send() can build history without a stale closure
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading, recordingPhase]);

  // Track whether a voice reply is in flight so live-mode transcripts don't
  // overlap or loop on our own spoken reply.
  useEffect(() => {
    voiceBusyRef.current = loading || preparingId !== null || speakingId !== null;
  }, [loading, preparingId, speakingId]);

  // stop any playback / hang up any live call / release recorder resources on unmount
  useEffect(
    () => () => {
      stopAudio();
      voiceCallRef.current?.end();
      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current);
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    []
  );

  // Resume a saved conversation (reopened from history, or the last-open one
  // on a plain reload) or start a fresh one. Skipped when a seed question was
  // passed (Home's "Try asking" starters always begin a new thread) or when
  // History's "+ Start new conversation" explicitly forces a fresh id.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const forcedNew = !!location.state?.isNew;
      let lastId = null;
      if (!forcedNew && !seed && historySupported) {
        try {
          lastId = localStorage.getItem(LAST_CONVERSATION_KEY);
        } catch {
          lastId = null;
        }
      }
      const incoming = forcedNew || seed ? null : location.state?.conversationId || lastId;

      if (incoming && historySupported) {
        const rec = await getConversation(incoming).catch(() => null);
        if (rec && !cancelled) {
          conversationIdRef.current = rec.id;
          conversationMetaRef.current = { startedAt: rec.startedAt || Date.now(), title: rec.title || null };
          const hydrated = (rec.messages || []).map((m) =>
            m.audioBlob ? { ...m, audioUrl: URL.createObjectURL(m.audioBlob) } : m
          );
          setMessages(hydrated);
          seeded.current = true; // nothing to seed when resuming a saved thread
          try {
            localStorage.setItem(LAST_CONVERSATION_KEY, rec.id);
          } catch {
            /* noop */
          }
          setHistoryReady(true);
          return;
        }
      }

      // No conversation to resume -- start a fresh one.
      const id = location.state?.conversationId || newMsgId();
      if (cancelled) return;
      conversationIdRef.current = id;
      conversationMetaRef.current = { startedAt: Date.now(), title: null };
      try {
        localStorage.setItem(LAST_CONVERSATION_KEY, id);
      } catch {
        /* noop */
      }
      setHistoryReady(true);
      if (historySupported) trimRetention(getProfileId(), MAX_CONVERSATIONS).catch(() => {});
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced upsert: persists the whole conversation (messages + audio blobs)
  // on every change, so a reload restores both transcript and playable audio.
  useEffect(() => {
    if (!historySupported || !historyReady || !conversationIdRef.current || messages.length === 0) return;
    const t = setTimeout(() => {
      const firstUserText = messages.find((m) => m.direction === 'inbound' && m.text)?.text;
      if (!conversationMetaRef.current.title && firstUserText) {
        conversationMetaRef.current.title = firstUserText.slice(0, 48);
      }
      const stored = messages.map(({ audioUrl: _audioUrl, ...rest }) => rest); // object URLs don't survive reload
      upsertConversation({
        id: conversationIdRef.current,
        profileId: getProfileId(),
        title: conversationMetaRef.current.title || 'Voice conversation',
        startedAt: conversationMetaRef.current.startedAt || Date.now(),
        updatedAt: Date.now(),
        messages: stored,
      }).catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [messages, historyReady]);

  // If we arrived with a seed question (from the landing page), ask it once.
  useEffect(() => {
    if (seed && !seeded.current) {
      seeded.current = true;
      send(seed);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Prompt 6 (barge-in): hard-stop whatever is currently producing audio,
  // across both reply pipelines, so a new turn can start immediately.
  // Deliberately does NOT touch `voiceBusyRef` -- the caller sets that right
  // before starting the new turn, so there's never a gap where a third
  // transcript could sneak in as "not busy". Sarvam's realtime call is
  // STT-only (see hooks/useVoiceCall.js), so there's no server-audio path to
  // stop here anymore -- just our own client-generated audio.
  const stopSpeakingNow = () => {
    speakReqRef.current++; // invalidate any in-flight/foreground synth (FinGuru path)
    stopAudio();
    setSpeakingId(null);
    setPreparingId(null);
    setLiveSpeaking(false);
    setOllamaThinking(false);
    setVoiceStatus('idle');
    ollamaAbortRef.current?.abort(); // streaming-Ollama path
  };

  const speakMessage = async (id, text) => {
    const token = ++speakReqRef.current;
    try {
      stopAudio();
      setSpeakingId(null);
      setPreparingId(id); // show the loader while we synthesize
      setVoiceStatus('speaking');
      const { blob, provider } = await synthesizeText(text, languageRef.current);
      if (token !== speakReqRef.current) return; // cancelled or superseded
      setPreparingId(null);
      setSpeakingId(id);
      updateMessageById(id, { ttsProvider: provider });
      playAudioBlob(blob, {
        onEnd: () => {
          if (token !== speakReqRef.current) return;
          setSpeakingId(null);
          setVoiceStatus('idle');
        },
      });
    } catch {
      if (token !== speakReqRef.current) return;
      setPreparingId(null);
      setSpeakingId(null);
      setVoiceStatus('idle');
      setVoiceNote('Could not play the spoken reply.');
    }
  };

  // Shared "ask the brain" step used by both typed/live-call sends and the
  // Prompt 2 voice-message queue, so the FinGuru-vs-Ollama-fallback choice
  // lives in exactly one place instead of being duplicated per call site.
  // `useOllamaBrain`, when explicitly passed (Prompt 7's text fallback),
  // overrides the normal voice-flag-based selection so a typed fallback
  // message is answered by whichever brain voice was actually configured to
  // use -- kept separate from `voice` (which only affects what's reported to
  // FinGuru/telemetry), not reused for both, since they mean different things.
  // `name` (the resolved session identity) is read from the ref here, once,
  // rather than threaded through every individual call site's options --
  // every brain request goes through this one function, so this is the only
  // place that needs to know about it.
  // `speechHooks`, when present, means the caller wants this turn SPOKEN as it
  // arrives rather than after it completes. Only the FinGuru brain honours it:
  // it has a streaming endpoint that emits whole sentences (see
  // api/finguruStream.js), so the first words play in ~2-4s instead of after
  // the full 12-95s answer. Ollama keeps its own streaming path
  // (streamOllamaToTTS), which is selected upstream, so nothing changes there.
  const askBrain = (text, history, { colloquial: coll, language: lang, voice, useOllamaBrain, speechHooks }) => {
    const useOllama = OLLAMA_ENABLED && (useOllamaBrain !== undefined ? useOllamaBrain : USE_OLLAMA_IN_VOICE && !!voice);
    if (!useOllama && speechHooks) {
      return askFinGuruStreaming(text, history, {
        colloquial: coll, language: lang, name: nameRef.current, ...speechHooks,
      });
    }
    return useOllama
      ? askOllama(text, history, { language: lang, colloquial: coll, voice: !!voice, name: nameRef.current })
      : askFinGuru(text, history, { colloquial: coll, language: lang, voice: !!voice, name: nameRef.current });
  };

  const send = async (text, opts = {}) => {
    const trimmed = (text ?? input).trim();
    if (!trimmed || loading) return;
    const history = messagesRef.current
      .filter((m) => m.text)
      .map((m) => ({ role: m.direction === 'inbound' ? 'user' : 'assistant', content: m.text }));
    setMessages((prev) => [
      ...prev,
      {
        id: newMsgId(),
        direction: 'inbound',
        text: trimmed,
        viaFallback: !!opts.viaFallback,
        ...(opts.sttProvider ? { sttProvider: opts.sttProvider } : {}),
      },
    ]);
    setInput('');
    setLoading(true);
    // Follow-up suggestions are a text-mode-only feature: a voice-originated
    // question (opts.speak) skips the instruction entirely, so the spoken
    // reply never has to read a suggestions block aloud or have one stripped
    // out of it.
    const wantFollowUps = followUpsEnabled && !opts.speak;
    const questionForBrain = wantFollowUps ? withFollowUpInstruction(trimmed) : trimmed;
    // Generated before the call, not after: when this turn streams, audio
    // starts while the reply is still being written, so the speaking state and
    // the TTS badge need an id to attach to before there is any reply text.
    const outboundId = newMsgId();
    // Captured by the streaming hooks below and folded into the message when
    // it is finally added -- updateMessageById cannot be used from inside the
    // stream, because the message does not exist in state yet.
    let streamedProvider = null;
    try {
      const res = await askBrain(questionForBrain, history, {
        colloquial: colloquialRef.current,
        language: languageRef.current,
        voice: !!opts.speak,
        // Prompt 7: a fallback-typed message uses whichever brain voice
        // questions are configured to use, regardless of USE_OLLAMA_IN_VOICE's
        // normal voice-only gating -- see askBrain's comment.
        useOllamaBrain: opts.viaFallback ? USE_OLLAMA_IN_VOICE : undefined,
        // Spoken turns stream; typed ones stay one-shot, since there is
        // nothing to play early and the full reply renders at once anyway.
        ...(opts.speak ? {
          speechHooks: {
            onFirstProvider: (provider) => { streamedProvider = provider; },
            onSpeakingChange: (speaking) => {
              setSpeakingId(speaking ? outboundId : null);
              setVoiceStatus(speaking ? 'speaking' : 'idle');
            },
          },
        } : {}),
      });
      if (opts.speak) clearFallbackIfReason('llm'); // a voice-originated reply succeeded -> LLM is fine
      const rawReply = res.text || "I couldn't find an answer for that — try rephrasing?";
      // Strip the follow-ups block back out before storing/displaying --
      // never leaked into message.text, so it can't pollute a later turn's
      // history (built from messagesRef.current's .text fields) either.
      // extractFollowUps still runs whenever the instruction was sent, because
      // its other job is stripping the marker block out of the displayed text
      // -- a brain that answers the instruction AND fills the schema field
      // would otherwise leave "###FOLLOWUPS###" sitting in the bubble.
      const { answer: replyText, followUps: parsedFollowUps } = wantFollowUps
        ? extractFollowUps(rawReply)
        : { answer: rawReply, followUps: [] };
      // FinGuru returns these as a schema field, which the model is
      // constrained to fill; the marker is what Ollama (no schema) produces,
      // and only sometimes. Prefer the reliable one, fall back to the other.
      const followUps = wantFollowUps
        ? (res.followUps?.length ? res.followUps : parsedFollowUps).slice(0, 3)
        : [];
      setMessages((prev) => [
        ...prev,
        {
          id: outboundId,
          direction: 'outbound',
          text: replyText,
          language: res.language,
          variant: res.hitl?.triggered ? 'error' : 'default',
          followUps,
          ...(streamedProvider ? { ttsProvider: streamedProvider } : {}),
          // Calculators the backend offered for this question -- an EMI card
          // under an EMI answer, a FIRE planner under a retirement one, each
          // prefilled with whatever the agent actually computed. Usually [].
          tools: res.tools || [],
        },
      ]);
      // Speak the reply back when the question came in by voice (or the
      // fallback banner said TTS still works). `res.spoken` means the
      // streaming path already played it sentence by sentence -- speaking it
      // again here would repeat the whole answer on top of itself.
      if (opts.speak && res.text && !res.spoken) speakMessage(outboundId, replyText);
    } catch {
      // Only a voice-originated send failing means the voice LLM path is
      // down -- a plain typed question failing is a normal error, not a
      // voice-pipeline problem worth switching the whole UI into fallback mode.
      if (opts.speak) enterFallback('llm', 'The assistant is temporarily unavailable — you can keep typing.', true);
      setMessages((prev) => [
        ...prev,
        { id: newMsgId(), direction: 'outbound', text: 'Something went wrong reaching FinGuru. Please try again.', variant: 'error' },
      ]);
    } finally {
      setLoading(false);
    }
  };

  // --- Prompt 2: record -> preview -> send (queued) -> transcript -> reply audio ---

  const updateMessageById = (id, patch) => {
    if (id == null) return;
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...(typeof patch === 'function' ? patch(m) : patch) } : m))
    );
  };

  const beginRecording = async () => {
    setVoiceNote(null);
    const ok = await recorder.start();
    if (ok) logMicStart({ mode: 'turn' }); // stage 1 (turn-mode push-to-talk)
    if (!ok) {
      // Inline, non-silent failure in the chat itself (not just the transient banner).
      setMessages((prev) => [
        ...prev,
        {
          id: newMsgId(),
          direction: 'outbound',
          variant: 'error',
          text: 'Microphone permission was denied (or no mic is available) — you can type your question instead.',
        },
      ]);
      setVoiceNote('Microphone unavailable or permission denied.');
      return;
    }
    setRecordingPhase('recording');
    setRecordingElapsed(0);
    recordingStartRef.current = Date.now();
    recordingTimerRef.current = setInterval(() => {
      setRecordingElapsed((Date.now() - recordingStartRef.current) / 1000);
    }, 200);
  };

  const stopToPreview = () => {
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    const elapsed = (Date.now() - recordingStartRef.current) / 1000;
    const blob = recorder.stop();
    if (!blob) {
      setVoiceNote('No audio captured — try again.');
      setRecordingPhase('idle');
      return;
    }
    previewBlobRef.current = blob;
    previewUrlRef.current = URL.createObjectURL(blob);
    setPreviewDuration(elapsed);
    setRecordingPhase('preview');
  };

  const discardRecording = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    previewBlobRef.current = null;
    setRecordingPhase('idle');
  };

  const reRecord = () => {
    discardRecording();
    beginRecording();
  };

  // Sequentially processes queued voice messages so brain/TTS calls never
  // race each other; each message still shows its own live status so a
  // second recording can be queued while the first is still processing.
  const runVoiceQueue = async () => {
    if (queueRunningRef.current) return;
    queueRunningRef.current = true;
    while (voiceQueueRef.current.length) {
      setQueueDepth(voiceQueueRef.current.length - 1);
      const job = voiceQueueRef.current.shift();
      // eslint-disable-next-line no-await-in-loop
      await processVoiceJob(job);
    }
    setQueueDepth(0);
    queueRunningRef.current = false;
  };

  // STT reports the language it actually heard (result.detected_language) --
  // reflect that in the picker (and languageRef, which the rest of THIS turn
  // reads synchronously) so the reply comes back and is spoken in the same
  // language the user spoke. Sarvam auto-detects fresh on every utterance
  // (both turn mode and live calls always request language_code="auto"/
  // "unknown" -- see api/sarvam.js and hooks/useVoiceCall.js), so this can
  // freely move the picker mid-call too, not just between calls.
  //
  // Returns 'applied' (switched to a newly-detected, supported language),
  // 'unchanged' (already on that language, or STT reported nothing usable --
  // proceed normally either way), or 'unsupported' (STT detected a language
  // that isn't one of ENABLED_LANGUAGES, or an unrecognized code entirely) --
  // callers must stop the turn on 'unsupported' rather than running the
  // brain/TTS in a language this app doesn't offer.
  const applyDetectedLanguage = (detected) => {
    if (!detected) return 'unchanged'; // STT didn't report a language -- don't block on it
    const code = resolveLanguageCode(detected);
    if (!code || !ENABLED_LANGUAGES.includes(code)) return 'unsupported';
    if (code === languageRef.current) return 'unchanged';
    languageRef.current = code; // used immediately below by askBrain/synthesizeText
    setLanguage(code); // updates the dropdown on the next render
    setVoiceNote(`Detected ${LANGUAGE_NAMES[code]} — switched the reply language to match.`);
    return 'applied';
  };

  const UNSUPPORTED_LANGUAGE_MESSAGE = "This language isn't supported yet — support will be added soon. Try English, Tamil, or Hindi.";

  const processVoiceJob = async ({ id, blob }) => {
    updateMessageById(id, { status: 'processing', statusText: 'Transcribing…' });
    let transcript = '';
    let langStatus;
    try {
      const result = await transcribeAudio(blob);
      transcript = (result?.text || '').trim();
      langStatus = applyDetectedLanguage(result?.detected_language);
      clearFallbackIfReason('stt'); // transcription succeeded -> STT is fine
    } catch (err) {
      const status = err?.response?.status;
      enterFallback('stt', 'Voice transcription is unavailable right now — you can type instead.', true);
      updateMessageById(id, {
        status: 'error',
        statusText: status ? `Couldn't transcribe (error ${status}).` : "Couldn't reach the voice server.",
      });
      return;
    }
    if (!transcript) {
      updateMessageById(id, { status: 'error', statusText: "Didn't catch that — try again." });
      return;
    }
    if (langStatus === 'unsupported') {
      updateMessageById(id, {
        text: transcript,
        status: 'error',
        statusText: UNSUPPORTED_LANGUAGE_MESSAGE,
        sttProvider: 'sarvam',
      });
      return;
    }
    updateMessageById(id, {
      text: transcript,
      status: 'processing',
      statusText: 'Getting a reply…',
      sttProvider: 'sarvam',
    });

    const history = messagesRef.current
      .filter((m) => m.text)
      .map((m) => ({ role: m.direction === 'inbound' ? 'user' : 'assistant', content: m.text }));

    let res;
    try {
      res = await askBrain(transcript, history, {
        colloquial: colloquialRef.current,
        language: languageRef.current,
        voice: true,
      });
      clearFallbackIfReason('llm');
    } catch {
      enterFallback('llm', 'The assistant is temporarily unavailable — you can keep typing.', true);
      updateMessageById(id, { status: 'error', statusText: 'Something went wrong reaching FinGuru.' });
      return;
    }
    updateMessageById(id, { status: 'done' });

    const replyText = res.text || "I couldn't find an answer for that — try rephrasing?";
    const replyId = newMsgId(); // generated up front -- no index-capture needed
    setMessages((prev) => [
      ...prev,
      {
        id: replyId,
        type: 'voice',
        direction: 'outbound',
        text: replyText,
        status: 'processing',
        statusText: 'Synthesizing reply…',
        variant: res.hitl?.triggered ? 'error' : 'default',
      },
    ]);

    try {
      const { blob: audioBlob, provider } = await synthesizeText(replyText, languageRef.current);
      const url = URL.createObjectURL(audioBlob);
      updateMessageById(replyId, {
        status: 'done',
        audioUrl: url,
        audioBlob,
        autoPlay: true,
        ttsProvider: provider,
      });
      clearFallbackIfReason('tts');
    } catch {
      // TTS failed but we still have the text -- degrade to a readable reply
      // rather than losing the answer.
      enterFallback('tts', 'Voice replies are unavailable right now — showing text instead.', false);
      updateMessageById(replyId, { status: 'error', statusText: 'Reply text only — voice playback failed.' });
    }
  };

  const sendVoiceMessage = () => {
    const blob = previewBlobRef.current;
    const url = previewUrlRef.current;
    const duration = previewDuration;
    if (!blob || !url) return;
    previewBlobRef.current = null;
    previewUrlRef.current = null;
    setRecordingPhase('idle');

    const id = newMsgId(); // generated up front -- no index-capture needed
    setMessages((prev) => [
      ...prev,
      { id, type: 'voice', direction: 'inbound', audioUrl: url, audioBlob: blob, duration, status: 'queued' },
    ]);
    voiceQueueRef.current.push({ id, blob });
    if (voiceQueueRef.current.length > 1) setQueueDepth(voiceQueueRef.current.length - 1);
    runVoiceQueue();
  };

  const languageSelect = (
    <select
      value={language}
      onChange={(e) => setLanguage(e.target.value)}
      disabled={inCall}
      aria-label="Response language"
      title={
        inCall
          ? "Sarvam auto-detects your language during a call — this updates automatically as you speak"
          : 'Language for replies and voice'
      }
      className={`h-7 rounded-full border border-outline-variant bg-surface text-[11px] font-heading font-bold text-primary pl-2 pr-1 outline-none ${
        inCall ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
      }`}
    >
      {ENABLED_LANGUAGES.map((code) => (
        <option key={code} value={code}>
          {LANGUAGE_NAMES[code]}
        </option>
      ))}
    </select>
  );

  const desiSwitch = (
    <button
      type="button"
      onClick={() => setColloquial((v) => !v)}
      role="switch"
      aria-checked={colloquial}
      aria-label="Desi mode: everyday street language instead of formal phrasing"
      title={
        colloquial
          ? 'Desi mode ON — replies in everyday, street-style language'
          : 'Desi mode OFF — replies in standard/formal language'
      }
      className="flex items-center gap-1 h-7 pl-2 pr-1 rounded-full border transition active:scale-95 whitespace-nowrap"
      style={{
        borderColor: colloquial ? 'var(--color-primary)' : 'var(--color-outline-variant)',
        background: colloquial ? 'var(--color-primary)' : 'transparent',
      }}
    >
      <span
        className="text-[10.5px] font-heading font-bold"
        style={{ color: colloquial ? 'var(--color-on-primary)' : 'var(--color-on-surface-variant)' }}
      >
        Desi
      </span>
      <span
        className="relative w-7 h-4 rounded-full transition"
        style={{ background: colloquial ? 'var(--color-on-primary)' : 'var(--color-outline-variant)' }}
      >
        <span
          className="absolute top-0.5 w-3 h-3 rounded-full transition-all"
          style={{
            left: colloquial ? '14px' : '2px',
            background: colloquial ? 'var(--color-primary)' : 'var(--color-surface-lowest)',
          }}
        />
      </span>
    </button>
  );

  // Recommended follow-up questions -- only ever applied to typed (text-mode)
  // questions, see send()'s wantFollowUps -- a voice question ignores this
  // toggle entirely.
  const followUpsSwitch = (
    <button
      type="button"
      onClick={() => setFollowUpsEnabled((v) => !v)}
      role="switch"
      aria-checked={followUpsEnabled}
      aria-label="Recommended follow-up questions for typed replies"
      title={
        followUpsEnabled
          ? 'Follow-up suggestions ON — typed replies show up to 3 suggested next questions'
          : 'Follow-up suggestions OFF'
      }
      className="flex items-center gap-1 h-7 pl-2 pr-1 rounded-full border transition active:scale-95 whitespace-nowrap"
      style={{
        borderColor: followUpsEnabled ? 'var(--color-primary)' : 'var(--color-outline-variant)',
        background: followUpsEnabled ? 'var(--color-primary)' : 'transparent',
      }}
    >
      <span
        className="text-[10.5px] font-heading font-bold"
        style={{ color: followUpsEnabled ? 'var(--color-on-primary)' : 'var(--color-on-surface-variant)' }}
      >
        Follow-ups
      </span>
      <span
        className="relative w-7 h-4 rounded-full transition"
        style={{ background: followUpsEnabled ? 'var(--color-on-primary)' : 'var(--color-outline-variant)' }}
      >
        <span
          className="absolute top-0.5 w-3 h-3 rounded-full transition-all"
          style={{
            left: followUpsEnabled ? '14px' : '2px',
            background: followUpsEnabled ? 'var(--color-primary)' : 'var(--color-surface-lowest)',
          }}
        />
      </span>
    </button>
  );

  const toggleSpeak = (id, text) => {
    // If this message is showing Preparing/Stop at all, SOMETHING is currently
    // producing its audio -- hard-stop via stopSpeakingNow() rather than trying
    // to match this exact id against whichever pipeline ref backs it. Matching
    // refs (e.g. ollamaMsgIdRef.current === id) can go stale the instant a
    // barge-in (real or a false one from mic echo re-hearing our own TTS)
    // swaps in a newer turn's controller, leaving Stop silently unable to find
    // "its" controller. stopSpeakingNow() tears down all three reply pipelines
    // unconditionally, so it works regardless of which one is actually live.
    if (preparingId === id || speakingId === id) {
      stopSpeakingNow();
      return;
    }
    speakMessage(id, text);
  };

  const toggleCall = () => {
    setVoiceNote(null);
    if (inCall) voiceCall.end();
    else voiceCall.start();
  };

  const transcribing = voiceStatus === 'transcribing';
  const liveMode = VOICE_MODE === 'live';

  // --- Prompt 5: derive the current pipeline stage from existing signals
  // (Listening -> Transcribing -> Thinking -> Speaking -> Idle) rather than
  // maintaining a parallel state machine that could drift from reality.
  // NOTE: the live-call server protocol has no discrete "speech ended, STT
  // started" event -- it only emits a final `transcript` once STT is already
  // done -- so a true, distinct "Transcribing" moment isn't observable in live
  // mode; the indicator goes Listening -> Thinking the instant a transcript
  // arrives. A backend `transcribing_started` event would close this gap.
  // "Thinking" covers: (a) the plain FinGuru/one-shot-Ollama reply path, where
  // `loading` (existing send() state) already tracks the brain call in flight;
  // (b) the streaming-Ollama path, via the new `ollamaThinking`. The pure
  // server-echo path (SKIP_FINGURU_IN_VOICE && !OLLAMA_FALLBACK_ACTIVE, i.e.
  // neither FinGuru nor Ollama configured) has no client-observable "thinking"
  // gap -- the server is a single opaque transcript-in/audio-out step there,
  // so that combination shows Listening -> Speaking with no Thinking tick in
  // between (documented gap, not a bug: no server event marks "now generating").
  const liveThinking = (loading && liveMode) || ollamaThinking;
  const liveSpeakingNow = liveSpeaking || speakingId !== null;
  const liveStage =
    !liveMode || !inCall || voiceCall.status !== 'live'
      ? null
      : liveSpeakingNow
        ? 'speaking'
        : liveThinking
          ? 'thinking'
          : 'listening';

  // Turn mode: reuse the currently-processing voice message's own status text
  // (already tracked per-message since Prompt 2) instead of a second parallel
  // stage tracker -- there's exactly one job "in flight" at a time (the queue
  // is sequential), so this is an accurate, zero-duplication source of truth.
  const activeVoiceJob = !liveMode ? messages.find((m) => m.type === 'voice' && m.status === 'processing') : null;
  const turnStage = recordingPhase === 'recording' ? 'listening' : activeVoiceJob ? activeVoiceJob.statusText : null;

  const headerControls = (
    <>
      {languageSelect}
      {desiSwitch}
      {followUpsSwitch}
    </>
  );

  if (!name) return <NamePrompt onSubmit={setName} />;

  return (
    <PhoneScreen title="FinGuru" right={headerControls}>
      {/* Bounded height (viewport minus PhoneScreen header h-14 + main py-5)
          so only the message list scrolls and the input stays pinned. */}
      <div className="flex flex-col h-[calc(100dvh-96px)]">
        {!nameHistoryChecked && (
          <p className="text-[11px] text-on-surface-variant px-1 pb-1.5 flex items-center gap-1.5 shrink-0">
            <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            Checking your history for {name}…
          </p>
        )}
        {nameHistoryChecked && nameHistory && nameHistory.length > 0 && (
          <div className="mb-2 bg-primary/5 border border-primary/20 rounded-lg px-3 py-2 shrink-0">
            <p className="text-[11.5px] font-semibold text-primary">
              📡 Backend history for {name} — {nameHistory.length} message{nameHistory.length === 1 ? '' : 's'}
            </p>
            <details className="mt-1">
              <summary className="text-[11px] text-primary cursor-pointer underline underline-offset-2">View</summary>
              <div className="mt-1.5 flex flex-col gap-1 max-h-40 overflow-y-auto">
                {nameHistory.map((m, i) => (
                  <p key={i} className="text-[11.5px] text-on-surface-variant">
                    <span className="font-semibold">{m.role || m.direction || 'message'}:</span>{' '}
                    {m.text || m.content || m.message || JSON.stringify(m)}
                  </p>
                ))}
              </div>
            </details>
          </div>
        )}
        <div className="flex-1 min-h-0 flex flex-col gap-3 overflow-y-auto pb-3">
          {messages.length === 0 && !loading && (
            <BotBubble>
              Hi, I'm FinGuru 💡 Ask me anything about saving, investing, tax, FD rates, loans, or government
              schemes in India. {liveMode ? 'Tap 📞 to start a voice call.' : 'Tap the mic to ask by voice.'}
            </BotBubble>
          )}
          {messages.map((m, i) => {
            if (m.type === 'voice') {
              return (
                <VoiceMessageBubble
                  key={i}
                  direction={m.direction}
                  audioUrl={m.audioUrl}
                  duration={m.duration}
                  text={m.text}
                  status={m.status}
                  statusText={m.statusText}
                  variant={m.variant}
                  autoPlay={!!m.autoPlay}
                  provider={m.sttProvider || m.ttsProvider}
                />
              );
            }
            return m.direction === 'inbound' ? (
              <div key={i} className="flex flex-col items-end gap-0.5">
                <UserBubble>{m.text}</UserBubble>
                {m.viaFallback && (
                  <span className="text-[10px] text-on-surface-variant mr-1" title="Sent as text while voice was unavailable">
                    ⌨️ typed (voice unavailable)
                  </span>
                )}
                {m.sttProvider && (
                  <span className="text-[10px] text-on-surface-variant mr-1 opacity-70">
                    STT: {providerLabel(m.sttProvider)}
                  </span>
                )}
              </div>
            ) : (
              <div key={i} className="flex flex-col gap-1 self-start max-w-[88%]">
                <BotBubble variant={m.variant}>
                  <Markdown text={m.text} />
                </BotBubble>
                <div className="flex items-center gap-2 ml-10">
                  <button
                    type="button"
                    onClick={() => toggleSpeak(m.id, m.text)}
                    className="self-start text-[11px] font-semibold text-primary flex items-center gap-1 active:scale-95"
                    title={preparingId === m.id ? 'Preparing audio…' : speakingId === m.id ? 'Stop' : 'Play aloud'}
                  >
                    {preparingId === m.id ? (
                      <>
                        <span className="inline-block w-3 h-3 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                        Preparing…
                      </>
                    ) : speakingId === m.id ? (
                      '⏹ Stop'
                    ) : (
                      '🔊 Play'
                    )}
                  </button>
                  {m.interrupted && (
                    <span className="text-[10.5px] text-on-surface-variant" title="You interrupted this reply">
                      ⏸ Interrupted
                    </span>
                  )}
                  {m.text && (
                    <span className="text-[10px] text-on-surface-variant opacity-70">
                      TTS: {providerLabel(m.ttsProvider || predictedTtsProvider(m.text))}
                    </span>
                  )}
                </div>
                {m.tools?.length > 0 && (
                  <div className="ml-10">
                    {m.tools.map((suggestion) => (
                      <ToolCard
                        key={suggestion.tool_id}
                        suggestion={suggestion}
                        name={nameRef.current}
                      />
                    ))}
                  </div>
                )}
                {m.followUps?.length > 0 && (
                  <div className="flex flex-col gap-1.5 ml-10 mt-0.5">
                    {m.followUps.map((q, qi) => (
                      <button
                        key={qi}
                        type="button"
                        disabled={loading}
                        onClick={() => send(q)}
                        className="text-left text-[12.5px] font-semibold text-primary bg-primary/5 border border-primary/20 rounded-xl px-3 py-1.5 active:scale-[0.99] transition disabled:opacity-50"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {loading && <TypingBubble />}
          <div ref={bottomRef} />
        </div>

        {liveMode && inCall && (
          <div
            className={`flex items-center justify-between gap-2 mb-2 rounded-lg pl-2 pr-1 py-1.5 ${
              voiceCall.status === 'reconnecting' ? 'bg-amber-500/10' : 'bg-primary/5'
            }`}
          >
            <p
              className={`text-[11.5px] font-semibold flex items-center gap-2 min-w-0 ${
                voiceCall.status === 'reconnecting' ? 'text-amber-600' : 'text-primary'
              }`}
            >
              <span
                className={`w-2 h-2 rounded-full shrink-0 ${
                  voiceCall.status === 'reconnecting'
                    ? 'bg-amber-500 animate-pulse'
                    : voiceCall.muted
                      ? 'bg-outline'
                      : 'bg-error animate-pulse'
                }`}
              />
              <span className="truncate flex items-center gap-1">
                {voiceCall.status === 'connecting' ? (
                  'Connecting…'
                ) : voiceCall.status === 'reconnecting' ? (
                  `Reconnecting… (${voiceCall.reconnectAttempt}/${voiceCall.reconnectMaxAttempts})`
                ) : liveStage === 'thinking' ? (
                  <>
                    <span className="inline-block w-3 h-3 rounded-full border-2 border-primary border-t-transparent animate-spin shrink-0" />
                    Thinking…
                  </>
                ) : liveStage === 'speaking' ? (
                  <>🔊 Speaking…</>
                ) : voiceCall.muted ? (
                  'On a voice call — mic muted'
                ) : (
                  'On a voice call — talk naturally'
                )}
              </span>
            </p>
            <button
              type="button"
              onClick={voiceCall.toggleMute}
              role="switch"
              aria-checked={voiceCall.muted}
              aria-label={voiceCall.muted ? 'Unmute microphone' : 'Mute microphone'}
              title={`${voiceCall.muted ? 'Unmute' : 'Mute'} microphone (M)`}
              className={`shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[13px] transition active:scale-95 ${
                voiceCall.muted
                  ? 'bg-error text-on-error'
                  : 'bg-surface text-primary border border-outline-variant'
              }`}
            >
              <MicIcon muted={voiceCall.muted} />
            </button>
            {/* Prompt 7: manual switch to text, available any time during a
                call -- not just when something's actually broken. */}
            {!voiceFallback && (
              <button
                type="button"
                onClick={() => enterFallback('manual', "You're on text now — tap 📞 anytime to talk instead.", true)}
                aria-label="Switch to typing instead of voice"
                title="Switch to text"
                className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[13px] bg-surface text-primary border border-outline-variant"
              >
                ⌨️
              </button>
            )}
          </div>
        )}

        {/* Prompt 7: text fallback banner -- shown for both auto-detected
            failures and the manual switch above. */}
        {voiceFallback && (
          <div className="flex items-center justify-between gap-2 mb-2 bg-surface-container-low rounded-lg px-3 py-2 border border-outline-variant">
            <p className="text-[11.5px] text-on-surface-variant flex-1">🎤➡️⌨️ {voiceFallback.message}</p>
            <button
              type="button"
              onClick={() => setVoiceFallback(null)}
              className="text-[11px] font-bold text-primary shrink-0 active:scale-95"
            >
              {voiceFallback.reason === 'manual' ? 'Back to voice' : 'Try voice again'}
            </button>
          </div>
        )}

        {/* The 'recording' phase has its own dedicated level-meter bar below,
            so this only covers the post-send pipeline (Transcribing/Thinking/
            Synthesizing) -- turnStage can't be 'listening' when phase is 'idle'. */}
        {!liveMode && recordingPhase === 'idle' && turnStage && (
          <p className="text-[11px] font-semibold text-primary mb-1 px-1 flex items-center gap-1.5">
            <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            {turnStage}
          </p>
        )}

        {!liveMode && queueDepth > 0 && (
          <p className="text-[11px] text-on-surface-variant mb-2 px-1">
            {queueDepth} more voice message{queueDepth > 1 ? 's' : ''} queued…
          </p>
        )}

        {voiceNote && (
          <p className="text-[11.5px] text-on-surface-variant mb-2 bg-surface-container-low rounded-lg px-2 py-1.5">
            {voiceNote}
          </p>
        )}

        {/* Turn-mode recording bar: replaces the composer while capturing audio. */}
        {!liveMode && recordingPhase === 'recording' && (
          <div className="flex items-center gap-3 bg-error-container/40 rounded-full px-4 py-2.5 border border-error/30">
            <span className="w-2.5 h-2.5 rounded-full bg-error animate-pulse shrink-0" />
            <LevelMeter level={recordingLevel} />
            <span className="text-[13px] tabular-nums text-on-surface font-semibold flex-1">
              {Math.floor(recordingElapsed / 60)}:{String(Math.floor(recordingElapsed % 60)).padStart(2, '0')}
            </span>
            <button
              type="button"
              onClick={stopToPreview}
              aria-label="Stop recording"
              title="Stop recording"
              className="w-9 h-9 rounded-full bg-error text-on-error flex items-center justify-center shrink-0"
            >
              ■
            </button>
          </div>
        )}

        {/* Turn-mode preview bar: listen back, discard/re-record, or send. */}
        {!liveMode && recordingPhase === 'preview' && (
          <div className="flex items-center gap-2 bg-surface-container-low rounded-full pl-3 pr-1 py-1.5 border border-outline-variant">
            <audio controls src={previewUrlRef.current} className="h-8 flex-1 min-w-0" />
            <button
              type="button"
              onClick={discardRecording}
              aria-label="Discard recording"
              title="Discard"
              className="w-8 h-8 rounded-full flex items-center justify-center text-error shrink-0"
            >
              🗑️
            </button>
            <button
              type="button"
              onClick={reRecord}
              aria-label="Re-record"
              title="Re-record"
              className="w-8 h-8 rounded-full flex items-center justify-center text-primary shrink-0"
            >
              🔄
            </button>
            <button
              type="button"
              onClick={sendVoiceMessage}
              aria-label="Send voice message"
              title="Send"
              className="w-9 h-9 rounded-full bg-primary text-on-primary flex items-center justify-center shrink-0"
            >
              ➤
            </button>
          </div>
        )}

        {(liveMode || recordingPhase === 'idle') && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!nameHistoryChecked) return; // per spec: no new message until the history check settles
              // Prompt 7: while on the voice fallback, route typed messages
              // through the same brain voice uses and speak the reply back
              // if TTS itself isn't the thing that's down.
              if (voiceFallback) send(undefined, { viaFallback: true, speak: voiceFallback.ttsOk });
              else send();
            }}
            className="flex items-center gap-2 bg-surface-container-low rounded-full px-3 py-2 border border-outline-variant"
          >
            {liveMode
              ? voiceCall.supported && (
                  <button
                    type="button"
                    onClick={toggleCall}
                    disabled={!nameHistoryChecked}
                    aria-label={inCall ? 'End voice call' : 'Start voice call'}
                    title={inCall ? 'End call' : 'Start voice call'}
                    className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 transition disabled:opacity-50 ${
                      inCall ? 'bg-error text-on-error animate-pulse' : 'bg-surface text-primary border border-outline-variant'
                    }`}
                  >
                    {inCall ? '■' : '📞'}
                  </button>
                )
              : recorder.supported && (
                  <button
                    type="button"
                    onClick={beginRecording}
                    disabled={!nameHistoryChecked}
                    aria-label="Record a voice message"
                    title="Hold to talk — tap to start recording"
                    className="w-9 h-9 rounded-full flex items-center justify-center shrink-0 transition bg-surface text-primary border border-outline-variant disabled:opacity-50"
                  >
                    🎤
                  </button>
                )}
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={!nameHistoryChecked}
              placeholder={!nameHistoryChecked ? 'Checking your history…' : inCall ? 'On a call — or type a question' : 'Ask about SIPs, PPF, tax…'}
              className="flex-1 bg-transparent border-none outline-none text-[14px] text-on-surface placeholder:text-outline disabled:opacity-70"
            />
            <button
              type="submit"
              disabled={loading || transcribing || !nameHistoryChecked}
              className="w-9 h-9 rounded-full bg-primary text-on-primary flex items-center justify-center shrink-0 disabled:opacity-50"
            >
              ➤
            </button>
          </form>
        )}
      </div>
    </PhoneScreen>
  );
}
