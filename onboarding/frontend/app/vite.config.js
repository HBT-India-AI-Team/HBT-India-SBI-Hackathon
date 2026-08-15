import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load .env (all keys, no VITE_ prefix filter) so the voice-server target +
  // key stay server-side in this proxy and never reach the client bundle.
  const env = loadEnv(mode, process.cwd(), '')

  // Port comes from ../../ports.config via run_react.bat (FRONTEND_PORT env),
  // falling back to Vite's default 5173 when run directly with `npm run dev`.
  const frontendPort = Number(process.env.FRONTEND_PORT) || 5173

  const voiceTarget = env.VOICE_SERVER_TARGET || 'https://dreamboat-bleep-childhood.ngrok-free.dev'
  const voiceKey = env.VOICE_SERVER_KEY || ''
  // Streaming-TTS WS path on the voice server (Prompt 3). Configurable so it can
  // be corrected without code changes once that endpoint is finalized/deployed.
  const ttsStreamPath = env.VOICE_TTS_STREAM_PATH || '/voice/tts/stream'

  // Sarvam's own API, called directly from the frontend (via this same-origin
  // proxy, so the subscription key stays server-side and never reaches the
  // browser bundle) -- NOT routed through the voice server. The voice server's
  // own /stt/sarvam and /tts/sarvam wrappers were dropped after confirming its
  // outbound HTTPS to api.sarvam.ai fails there (corporate TLS-inspection proxy
  // the AI PC sits behind rejects Sarvam's cert chain); the browser's own
  // network path has no such interception.
  const sarvamTarget = 'https://api.sarvam.ai'
  const sarvamKey = env.SARVAM_API_KEY || ''

  return {
    plugins: [react()],
    server: {
      port: frontendPort,
      // Same-origin proxy to the reference voice server. The browser calls
      // /voice-api/* (no CORS/preflight); Vite forwards to <target>/voice/*
      // with the Bearer token attached here.
      proxy: {
        '/voice-api': {
          target: voiceTarget,
          changeOrigin: true,
          secure: true,
          rewrite: (p) => p.replace(/^\/voice-api/, '/voice'),
          headers: {
            'ngrok-skip-browser-warning': 'true',
            ...(voiceKey ? { Authorization: `Bearer ${voiceKey}` } : {}),
          },
        },
        // Streaming-TTS WebSocket for the Ollama-fallback voice path -- the
        // voice server's own local (Parler-TTS) streaming synth, never Sarvam.
        '/tts-ws': {
          target: voiceTarget,
          changeOrigin: true,
          secure: true,
          ws: true,
          rewrite: () => `${ttsStreamPath}${voiceKey ? `?token=${voiceKey}` : ''}`,
          headers: { 'ngrok-skip-browser-warning': 'true' },
        },
        // Sarvam batch STT (/speech-to-text) and TTS (/text-to-speech) REST
        // calls. Same-origin /sarvam-api/* -> https://api.sarvam.ai/* with the
        // subscription key attached here.
        '/sarvam-api': {
          target: sarvamTarget,
          changeOrigin: true,
          secure: true,
          rewrite: (p) => p.replace(/^\/sarvam-api/, ''),
          headers: sarvamKey ? { 'api-subscription-key': sarvamKey } : {},
        },
        // Sarvam's realtime STT WebSocket (saaras:v3-realtime), used for live
        // calls. The browser opens same-origin ws://<host>/sarvam-stt-ws?<query>;
        // Vite upgrades and proxies straight to Sarvam with the subscription
        // key attached server-side (browsers can't set custom WS handshake
        // headers, so this proxy is what keeps the key out of the client).
        '/sarvam-stt-ws': {
          target: sarvamTarget,
          changeOrigin: true,
          secure: true,
          ws: true,
          rewrite: (p) => p.replace(/^\/sarvam-stt-ws/, '/speech-to-text-realtime/ws'),
          headers: sarvamKey ? { 'api-subscription-key': sarvamKey } : {},
        },
      },
    },
  }
})
