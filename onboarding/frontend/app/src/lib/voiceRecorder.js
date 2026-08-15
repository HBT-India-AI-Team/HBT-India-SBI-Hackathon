import { useCallback, useRef, useState } from 'react';
import { CALL_INPUT_SAMPLE_RATE, floatTo16kPCM16 } from './liveCall';

// Records the mic via Web Audio and produces a 16kHz mono 16-bit WAV blob --
// exactly the format the reference voice client uses (YONO_CLIENT_SAMPLE_RATE
// = 16000), which is the safest input for the server's /transcribe.

export const recorderSupported =
  typeof window !== 'undefined' &&
  !!navigator.mediaDevices?.getUserMedia &&
  !!(window.AudioContext || window.webkitAudioContext);

/** Wrap Int16 PCM samples in a minimal RIFF/WAVE container. */
function encodeWav(int16, sampleRate) {
  const buffer = new ArrayBuffer(44 + int16.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  const dataLen = int16.length * 2;
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + dataLen, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // audio format = PCM
  view.setUint16(22, 1, true); // channels = mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, dataLen, true);
  let offset = 44;
  for (let i = 0; i < int16.length; i++, offset += 2) view.setInt16(offset, int16[i], true);
  return new Blob([view], { type: 'audio/wav' });
}

/**
 * `onLevel(rms)` (optional) fires with each audio callback's RMS level
 * (0..~1) while recording -- used to drive a live level meter. Everything
 * else about the hook's API/behavior is unchanged.
 */
export function useVoiceRecorder(onLevel) {
  const [recording, setRecording] = useState(false);
  const ctxRef = useRef(null);
  const streamRef = useRef(null);
  const processorRef = useRef(null);
  const chunksRef = useRef([]); // array of Float32Array at native rate
  const nativeRateRef = useRef(CALL_INPUT_SAMPLE_RATE);
  const onLevelRef = useRef(onLevel);
  onLevelRef.current = onLevel;

  const cleanup = useCallback(() => {
    try {
      processorRef.current?.disconnect();
    } catch {
      /* noop */
    }
    processorRef.current = null;
    try {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    } catch {
      /* noop */
    }
    streamRef.current = null;
    try {
      if (ctxRef.current && ctxRef.current.state !== 'closed') ctxRef.current.close();
    } catch {
      /* noop */
    }
    ctxRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (!recorderSupported || ctxRef.current) return false;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return false; // permission denied / no device
    }
    streamRef.current = stream;
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    ctxRef.current = ctx;
    nativeRateRef.current = ctx.sampleRate;
    chunksRef.current = [];

    const source = ctx.createMediaStreamSource(stream);
    // ScriptProcessorNode: deprecated but universally available without a
    // separate AudioWorklet module file -- same choice as useLiveCall.js.
    const processor = ctx.createScriptProcessor(4096, 1, 1);
    processorRef.current = processor;
    processor.onaudioprocess = (e) => {
      const data = e.inputBuffer.getChannelData(0);
      chunksRef.current.push(new Float32Array(data));
      if (onLevelRef.current) {
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
        onLevelRef.current(Math.sqrt(sum / data.length));
      }
    };
    source.connect(processor);
    const silent = ctx.createGain();
    silent.gain.value = 0;
    processor.connect(silent);
    silent.connect(ctx.destination);

    setRecording(true);
    return true;
  }, []);

  /** Stop and return a 16kHz mono WAV Blob (or null if nothing captured). */
  const stop = useCallback(() => {
    const nativeRate = nativeRateRef.current;
    const chunks = chunksRef.current;
    chunksRef.current = [];
    cleanup();
    setRecording(false);
    onLevelRef.current?.(0);

    const total = chunks.reduce((n, c) => n + c.length, 0);
    if (!total) return null;
    const merged = new Float32Array(total);
    let off = 0;
    for (const c of chunks) {
      merged.set(c, off);
      off += c.length;
    }
    const pcm16 = floatTo16kPCM16(merged, nativeRate);
    return encodeWav(pcm16, CALL_INPUT_SAMPLE_RATE);
  }, [cleanup]);

  const cancel = useCallback(() => {
    chunksRef.current = [];
    cleanup();
    setRecording(false);
    onLevelRef.current?.(0);
  }, [cleanup]);

  return { supported: recorderSupported, recording, start, stop, cancel };
}
