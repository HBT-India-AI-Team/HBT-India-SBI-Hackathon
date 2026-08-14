// Headless test for the live-call feature's pure logic + WS fallback behavior.
// Run with: node scripts/test_live_call.mjs
// Uses the real `ws` package (installed as a transient dev dependency via
// `npm install --no-save ws`) to spin up a tiny local WebSocket server that
// mimics the backend's documented failure modes, and a real browser-shaped
// `WebSocket` client (also from `ws`) to drive src/lib/liveCall.js's pure
// helpers exactly as useLiveCall.js would.
import assert from 'node:assert/strict';
import { WebSocketServer, WebSocket } from 'ws';
import {
  getLiveCallWsUrl,
  shouldFallbackToMock,
  fallbackReasonForCloseCode,
  ReplyAudioAssembler,
  PCMFrameBuffer,
  floatTo16kPCM16,
  CALL_FRAME_SAMPLES,
  CALL_FRAME_BYTES,
} from '../src/lib/liveCall.js';

let passed = 0;
function ok(name) {
  passed++;
  console.log(`  ok - ${name}`);
}

// ---------------------------------------------------------------------------
// (a) WS URL construction
// ---------------------------------------------------------------------------
console.log('(a) WS URL construction');
assert.equal(getLiveCallWsUrl('http://localhost:8000', 'sess-123'), 'ws://localhost:8000/sessions/sess-123/call/live');
ok('http:// -> ws://');
assert.equal(getLiveCallWsUrl('https://api.example.com/', 'sess-abc'), 'wss://api.example.com/sessions/sess-abc/call/live');
ok('https:// -> wss:// (and trailing slash stripped)');
assert.equal(getLiveCallWsUrl(null, 'x'), null);
ok('missing apiBase -> null (no throw)');

// ---------------------------------------------------------------------------
// (b) close-code fallback decision
// ---------------------------------------------------------------------------
console.log('(b) fallback-to-mock close-code handling');
assert.equal(shouldFallbackToMock(4503), true);
assert.equal(shouldFallbackToMock(4404), true);
assert.equal(shouldFallbackToMock(1006), true); // abnormal closure
assert.equal(shouldFallbackToMock(1000), false); // normal user-initiated close
ok('4503/4404/abnormal -> fallback; 1000 -> no fallback');
assert.match(fallbackReasonForCloseCode(4503, 'voice server unreachable'), /unreachable/i);
assert.match(fallbackReasonForCloseCode(4404, 'session_not_found'), /not.*found/i);
ok('human-readable fallback reasons for known codes');

// ---------------------------------------------------------------------------
// (b real integration) actually connect to a local `ws` server that closes
// with 4503 immediately after accepting, exactly like the backend does when
// the upstream voice server is unreachable, and verify the client-side
// logic reaches the "should fall back" conclusion without throwing.
// ---------------------------------------------------------------------------
console.log('(b-integration) live socket connect + 4503 close from a real ws server');
await new Promise((resolve, reject) => {
  const wss = new WebSocketServer({ port: 0 });
  wss.on('connection', (socket) => {
    socket.close(4503, 'voice server unreachable');
  });
  wss.on('listening', () => {
    const port = wss.address().port;
    const url = `ws://127.0.0.1:${port}/sessions/test-session/call/live`;
    assert.equal(url, getLiveCallWsUrl(`http://127.0.0.1:${port}`, 'test-session'));
    const client = new WebSocket(url);
    let fellBack = false;
    client.on('close', (code, reasonBuf) => {
      try {
        const reason = reasonBuf.toString();
        assert.equal(code, 4503);
        if (shouldFallbackToMock(code)) {
          fellBack = true;
        }
        assert.equal(fellBack, true, 'client must decide to fall back to mock UI on 4503');
        assert.match(fallbackReasonForCloseCode(code, reason), /unreachable/i);
        ok('client connected, received 4503 close, and correctly decided to fall back (no throw/hang)');
        wss.close(() => resolve());
      } catch (e) {
        wss.close(() => reject(e));
      }
    });
    client.on('error', (e) => {
      // Some environments raise a socket error alongside the abnormal close;
      // that's fine as long as 'close' still fires and we still fall back.
    });
  });
  wss.on('error', reject);
});

// ---------------------------------------------------------------------------
// (b real integration #2) session-not-found -> 4404
// ---------------------------------------------------------------------------
console.log('(b-integration) 4404 close (session not found)');
await new Promise((resolve, reject) => {
  const wss = new WebSocketServer({ port: 0 });
  wss.on('connection', (socket) => socket.close(4404, 'session_not_found'));
  wss.on('listening', () => {
    const port = wss.address().port;
    const client = new WebSocket(`ws://127.0.0.1:${port}/sessions/bogus/call/live`);
    client.on('close', (code, reasonBuf) => {
      try {
        assert.equal(code, 4404);
        assert.equal(shouldFallbackToMock(code), true);
        assert.match(fallbackReasonForCloseCode(code, reasonBuf.toString()), /could not be found/i);
        ok('4404 close correctly triggers fallback with a sensible reason');
        wss.close(() => resolve());
      } catch (e) {
        wss.close(() => reject(e));
      }
    });
    client.on('error', () => {});
  });
  wss.on('error', reject);
});

// ---------------------------------------------------------------------------
// (c) reply_audio_start / binary* / reply_audio_end parsing into a "play" event
// ---------------------------------------------------------------------------
console.log('(c) reply audio frame assembly (reply_audio_start -> binary* -> reply_audio_end)');
{
  const assembler = new ReplyAudioAssembler();
  assert.equal(assembler.handleJson({ type: 'reply_audio_start', sampling_rate: 24000 }), null);
  assert.equal(assembler.isActive, true);
  ok('reply_audio_start opens an active accumulation window');

  // Two binary frames of PCM16 samples: [1,2,3,4] and [5,6,7,8] (little-endian Int16)
  const frame1 = new Int16Array([1, 2, 3, 4]);
  const frame2 = new Int16Array([5, 6, 7, 8]);
  assembler.handleBinary(frame1.buffer);
  assembler.handleBinary(frame2.buffer);
  ok('binary frames accepted while active, buffered internally');

  const result = assembler.handleJson({ type: 'reply_audio_end' });
  assert.ok(result, 'reply_audio_end should yield a play event');
  assert.equal(result.type, 'play');
  assert.equal(result.samplingRate, 24000);
  assert.deepEqual(Array.from(result.samples), [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.equal(assembler.isActive, false);
  ok('reply_audio_end merges all buffered binary frames in order at the announced sampling rate, and closes the window');

  // Binary frames arriving outside an active window must be dropped, not throw.
  assembler.handleBinary(new Int16Array([9, 9]).buffer);
  const strayEnd = assembler.handleJson({ type: 'reply_audio_end' });
  assert.equal(strayEnd, null);
  ok('stray binary/END frames outside an active window are ignored safely');
}

// ---------------------------------------------------------------------------
// PCM frame sizing sanity: 30ms @ 16kHz mono = 480 samples = 960 bytes
// ---------------------------------------------------------------------------
console.log('(extra) outbound PCM frame sizing matches backend contract exactly');
assert.equal(CALL_FRAME_SAMPLES, 480);
assert.equal(CALL_FRAME_BYTES, 960);
ok('480 samples / 960 bytes per 30ms frame at 16kHz mono PCM16');

{
  const buf = new PCMFrameBuffer();
  // Simulate a native 48kHz mic delivering a 2048-sample chunk (typical
  // ScriptProcessorNode buffer size) -> downsample to 16kHz -> chunk into
  // fixed 480-sample frames.
  const native = new Float32Array(2048).map((_, i) => Math.sin(i / 20));
  const pcm16 = floatTo16kPCM16(native, 48000);
  const frames = buf.push(pcm16);
  for (const f of frames) {
    assert.equal(f.length, CALL_FRAME_SAMPLES);
    assert.equal(f.byteLength, CALL_FRAME_BYTES);
  }
  ok(`downsample 48kHz->16kHz + fixed-size chunking produced ${frames.length} correctly-sized 480-sample frames (remainder buffered for next push)`);
}

console.log(`\nAll ${passed} assertions passed.`);
process.exit(0);
