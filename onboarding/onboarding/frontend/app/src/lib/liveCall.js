// src/lib/liveCall.js
//
// Pure, DOM-free helpers for the live voice call feature (SupportCall.jsx /
// useLiveCall.js). Kept dependency-free from `window`/`AudioContext`/`WebSocket`
// so they can be unit-tested head-lessly under plain Node (see
// scripts/test_live_call.mjs) without jsdom/Playwright.

/** Audio protocol constants (must match backend/routers/calls.py + upstream voice server). */
export const CALL_INPUT_SAMPLE_RATE = 16000;
export const CALL_FRAME_MS = 30;
export const CALL_FRAME_SAMPLES = (CALL_INPUT_SAMPLE_RATE * CALL_FRAME_MS) / 1000; // 480
export const CALL_FRAME_BYTES = CALL_FRAME_SAMPLES * 2; // 960 (Int16)

/**
 * Derive the ws(s):// live-call URL from the REST API base URL and a session id.
 * http:// -> ws://, https:// -> wss://
 */
export function getLiveCallWsUrl(apiBase, sessionId) {
  if (!apiBase || !sessionId) return null;
  const wsBase = apiBase.replace(/^http:\/\//i, 'ws://').replace(/^https:\/\//i, 'wss://');
  return `${wsBase.replace(/\/+$/, '')}/sessions/${encodeURIComponent(sessionId)}/call/live`;
}

/** Known "give up on the live call, use the mock flow" close codes from the backend. */
export const FALLBACK_CLOSE_CODES = new Set([4503, 4404]);

/**
 * Decide whether a WebSocket close event should trigger falling back to the
 * mocked call UI/flow rather than treating it as a normal end-of-call.
 * - 1000 (normal closure) and 1005 (no status, e.g. after a clean local close)
 *   are NOT fallback-worthy -- those are ordinary hangups.
 * - Everything else (4503 unreachable, 4404 not found, abnormal 1006, etc.)
 *   is treated as a failure to establish/maintain the live call -> fallback.
 */
export function shouldFallbackToMock(closeCode) {
  if (closeCode === 1000 || closeCode === 1005) return false;
  return true;
}

export function fallbackReasonForCloseCode(code, reason) {
  if (code === 4503) return 'Voice server is unreachable right now.';
  if (code === 4404) return 'Call session could not be found.';
  if (reason) return reason;
  return 'Live call connection was lost.';
}

/**
 * Downsample + convert a Float32 audio buffer (as produced by the Web Audio
 * API at the AudioContext's native sample rate) into 16-bit PCM at
 * CALL_INPUT_SAMPLE_RATE. Returns an Int16Array.
 */
export function floatTo16kPCM16(float32Input, inputSampleRate) {
  const ratio = inputSampleRate / CALL_INPUT_SAMPLE_RATE;
  const outLength = Math.floor(float32Input.length / ratio);
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcIndex = i * ratio;
    const i0 = Math.floor(srcIndex);
    const i1 = Math.min(i0 + 1, float32Input.length - 1);
    const frac = srcIndex - i0;
    const sample = float32Input[i0] * (1 - frac) + float32Input[i1] * frac;
    const clamped = Math.max(-1, Math.min(1, sample));
    out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return out;
}

/**
 * A small stateful chunker: accepts a stream of Float32 samples at the
 * mic's native rate (already downsampled to 16kHz Int16 by the caller, OR
 * pass raw and it will just buffer) and yields fixed-size CALL_FRAME_SAMPLES
 * Int16 frames as they become available. Used so we always send exactly
 * 480-sample (960-byte) frames to match the backend's expected format,
 * regardless of how many samples a given AudioWorklet/ScriptProcessor
 * callback happens to deliver.
 */
export class PCMFrameBuffer {
  constructor(frameSamples = CALL_FRAME_SAMPLES) {
    this.frameSamples = frameSamples;
    this._pending = new Int16Array(0);
  }

  /** Push new Int16 samples; returns an array of full Int16Array frames ready to send. */
  push(int16Samples) {
    const combined = new Int16Array(this._pending.length + int16Samples.length);
    combined.set(this._pending, 0);
    combined.set(int16Samples, this._pending.length);

    const frames = [];
    let offset = 0;
    while (combined.length - offset >= this.frameSamples) {
      frames.push(combined.slice(offset, offset + this.frameSamples));
      offset += this.frameSamples;
    }
    this._pending = combined.slice(offset);
    return frames;
  }
}

/**
 * Pure state machine that parses the {reply_audio_start} -> binary* ->
 * {reply_audio_end} sequence described in the backend contract into
 * discrete "play this buffer at this sample rate" events, without touching
 * any real Audio API. Feed it decoded JSON objects (for text frames) and
 * raw byte lengths/Int16Arrays (for binary frames) via `handleJson` /
 * `handleBinary`; call `handleJson({type:'reply_audio_end'})` to flush.
 */
export class ReplyAudioAssembler {
  constructor() {
    this._active = false;
    this._samplingRate = null;
    this._chunks = [];
  }

  /** @returns {null | {type:'play', samplingRate:number, samples:Int16Array}} */
  handleJson(msg) {
    if (!msg || typeof msg !== 'object') return null;
    if (msg.type === 'reply_audio_start') {
      this._active = true;
      this._samplingRate = msg.sampling_rate;
      this._chunks = [];
      return null;
    }
    if (msg.type === 'reply_audio_end') {
      if (!this._active) return null;
      const totalLen = this._chunks.reduce((n, c) => n + c.length, 0);
      const merged = new Int16Array(totalLen);
      let off = 0;
      for (const c of this._chunks) {
        merged.set(c, off);
        off += c.length;
      }
      this._active = false;
      const samplingRate = this._samplingRate;
      this._chunks = [];
      this._samplingRate = null;
      return { type: 'play', samplingRate, samples: merged };
    }
    return null;
  }

  /** @param {ArrayBuffer} arrayBuffer raw PCM16 bytes from a binary WS frame */
  handleBinary(arrayBuffer) {
    if (!this._active) return;
    // Bytes -> Int16Array (little-endian, matches how the PCM was produced).
    const view = new DataView(arrayBuffer);
    const samples = new Int16Array(arrayBuffer.byteLength / 2);
    for (let i = 0; i < samples.length; i++) {
      samples[i] = view.getInt16(i * 2, true);
    }
    this._chunks.push(samples);
  }

  get isActive() {
    return this._active;
  }
}
