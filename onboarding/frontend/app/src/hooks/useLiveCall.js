import { useCallback, useRef, useState } from 'react';
import {
  PCMFrameBuffer,
  ReplyAudioAssembler,
  fallbackReasonForCloseCode,
  floatTo16kPCM16,
  getLiveCallWsUrl,
  shouldFallbackToMock,
} from '../lib/liveCall';

/**
 * Manages a real live voice call over `WS {API_BASE}/sessions/{id}/call/live`:
 * mic capture -> 30ms PCM16 frames out, JSON control + PCM16 reply audio in.
 *
 * This is an enhancement attempt layered on top of the existing mocked
 * call flow -- SupportCall.jsx still calls the REST /call/initiate and
 * /call/end endpoints itself; this hook only owns the WS + audio I/O, and
 * reports failures via onFallback() so the caller can drop back to the
 * mocked timer UI instead of getting stuck.
 */
export function useLiveCall({ apiBase, sessionId, onFallback }) {
  const [status, setStatusState] = useState('idle'); // idle | connecting | live | ended
  const statusRef = useRef('idle');
  const setStatus = useCallback((next) => {
    statusRef.current = next;
    setStatusState(next);
  }, []);
  const [transcript, setTranscript] = useState([]); // [{from:'user'|'agent', text}]
  const [muted, setMuted] = useState(false);
  const [lastLatencyMs, setLastLatencyMs] = useState(null);

  const wsRef = useRef(null);
  const micContextRef = useRef(null);
  const micStreamRef = useRef(null);
  const processorRef = useRef(null);
  const frameBufferRef = useRef(new PCMFrameBuffer());
  const assemblerRef = useRef(new ReplyAudioAssembler());
  const playbackContextRef = useRef(null);
  const nextPlayTimeRef = useRef(0);
  const mutedRef = useRef(false);
  const endedRef = useRef(false);

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
      if (micContextRef.current && micContextRef.current.state !== 'closed') {
        micContextRef.current.close();
      }
    } catch {
      /* noop */
    }
    micContextRef.current = null;
  }, []);

  const teardown = useCallback(
    (finalStatus) => {
      cleanupAudio();
      try {
        if (playbackContextRef.current && playbackContextRef.current.state !== 'closed') {
          playbackContextRef.current.close();
        }
      } catch {
        /* noop */
      }
      playbackContextRef.current = null;
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
    [cleanupAudio, setStatus]
  );

  const playReplyAudio = useCallback((samplingRate, int16Samples) => {
    if (!samplingRate || int16Samples.length === 0) return;
    if (!playbackContextRef.current) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      playbackContextRef.current = new Ctx();
      nextPlayTimeRef.current = playbackContextRef.current.currentTime;
    }
    const ctx = playbackContextRef.current;
    const float32 = new Float32Array(int16Samples.length);
    for (let i = 0; i < int16Samples.length; i++) {
      float32[i] = int16Samples[i] / (int16Samples[i] < 0 ? 0x8000 : 0x7fff);
    }
    const buffer = ctx.createBuffer(1, float32.length, samplingRate);
    buffer.copyToChannel(float32, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, nextPlayTimeRef.current);
    source.start(startAt);
    nextPlayTimeRef.current = startAt + buffer.duration;
  }, []);

  const handleServerJson = useCallback(
    (msg) => {
      switch (msg.type) {
        case 'ready':
          setStatus('live');
          break;
        case 'transcript':
          setTranscript((prev) => [...prev, { from: 'user', text: msg.text }]);
          break;
        case 'reply_text':
          setTranscript((prev) => [...prev, { from: 'agent', text: msg.text }]);
          break;
        case 'reply_audio_start':
        case 'reply_audio_end': {
          const result = assemblerRef.current.handleJson(msg);
          if (result) playReplyAudio(result.samplingRate, result.samples);
          break;
        }
        case 'latency':
          setLastLatencyMs(msg.round_trip_ms ?? null);
          break;
        case 'call_ended':
          endedRef.current = true;
          teardown('ended');
          break;
        default:
          break;
      }
    },
    [playReplyAudio, teardown, setStatus]
  );

  const startMicCapture = useCallback((ws) => {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    micContextRef.current = ctx;
    const source = ctx.createMediaStreamSource(micStreamRef.current);
    // ScriptProcessorNode: deprecated but universally supported without
    // shipping a separate AudioWorklet module file -- chosen deliberately
    // for shipping speed/reliability per the task brief.
    const bufferSize = 2048;
    const processor = ctx.createScriptProcessor(bufferSize, 1, 1);
    processorRef.current = processor;
    processor.onaudioprocess = (event) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      const input = event.inputBuffer.getChannelData(0);
      const source16k = mutedRef.current ? new Float32Array(input.length) : input;
      const pcm16 = floatTo16kPCM16(source16k, ctx.sampleRate);
      const frames = frameBufferRef.current.push(pcm16);
      for (const frame of frames) {
        ws.send(frame.buffer);
      }
    };
    source.connect(processor);
    // Some browsers require the processor to be connected to a destination
    // to fire onaudioprocess; route through a silent gain so nothing is heard.
    const silentGain = ctx.createGain();
    silentGain.gain.value = 0;
    processor.connect(silentGain);
    silentGain.connect(ctx.destination);
  }, []);

  const start = useCallback(async () => {
    if (!apiBase || !sessionId) {
      onFallback?.('missing_session');
      return;
    }
    endedRef.current = false;
    setStatus('connecting');
    setTranscript([]);

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      onFallback?.('mic_permission_denied');
      return;
    }
    micStreamRef.current = stream;

    const wsUrl = getLiveCallWsUrl(apiBase, sessionId);
    if (!wsUrl) {
      cleanupAudio();
      onFallback?.('bad_ws_url');
      return;
    }

    let ws;
    try {
      ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
    } catch {
      cleanupAudio();
      onFallback?.('ws_construct_failed');
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      try {
        startMicCapture(ws);
      } catch {
        // If mic capture setup fails post-connect, still let the call try
        // to proceed for playback-only; not worth failing the whole call.
      }
    };

    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        let msg;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        handleServerJson(msg);
      } else if (event.data instanceof ArrayBuffer) {
        assemblerRef.current.handleBinary(event.data);
      } else if (event.data && typeof event.data.arrayBuffer === 'function') {
        event.data.arrayBuffer().then((buf) => assemblerRef.current.handleBinary(buf));
      }
    };

    ws.onerror = () => {
      // onclose will fire right after with a code we can act on; nothing to do here.
    };

    ws.onclose = (event) => {
      if (endedRef.current) return; // already handled via call_ended path
      cleanupAudio();
      wsRef.current = null;
      if (statusRef.current === 'ended') return;
      if (shouldFallbackToMock(event.code)) {
        const reason = fallbackReasonForCloseCode(event.code, event.reason);
        onFallback?.(reason);
      } else {
        setStatus('ended');
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, sessionId, onFallback, startMicCapture, handleServerJson, cleanupAudio]);

  const end = useCallback(() => {
    endedRef.current = true;
    teardown('ended');
  }, [teardown]);

  const toggleMute = useCallback(() => {
    mutedRef.current = !mutedRef.current;
    setMuted(mutedRef.current);
  }, []);

  return { status, transcript, muted, toggleMute, start, end, lastLatencyMs };
}

export default useLiveCall;
