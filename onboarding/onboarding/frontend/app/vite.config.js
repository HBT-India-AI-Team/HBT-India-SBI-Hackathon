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
        // WebSocket for the live-call voice mode. The browser opens a
        // same-origin ws://<host>/voice-ws; Vite upgrades and proxies it to
        // <target>/voice/call?token=<key>, so the token never reaches the
        // client. Only used when VITE_VOICE_MODE=live.
        '/voice-ws': {
          target: voiceTarget,
          changeOrigin: true,
          secure: true,
          ws: true,
          // Forward the client's selected language (from /voice-ws?lang=..) onto
          // /voice/call alongside the server-side token, so the live call starts
          // in the right language. Token stays server-side. URLSearchParams
          // guarantees the required `?token=..&lang=..` shape (one `?`, then
          // `&`) -- a second `?` would make lang invisible to the server.
          rewrite: (path) => {
            const qIndex = path.indexOf('?')
            const lang = qIndex >= 0 ? new URLSearchParams(path.slice(qIndex + 1)).get('lang') : null
            const params = new URLSearchParams()
            if (voiceKey) params.set('token', voiceKey)
            if (lang) params.set('lang', lang)
            const qs = params.toString()
            return `/voice/call${qs ? `?${qs}` : ''}`
          },
          headers: { 'ngrok-skip-browser-warning': 'true' },
        },
        // Streaming-TTS WebSocket for the Ollama-fallback voice path.
        '/tts-ws': {
          target: voiceTarget,
          changeOrigin: true,
          secure: true,
          ws: true,
          rewrite: () => `${ttsStreamPath}${voiceKey ? `?token=${voiceKey}` : ''}`,
          headers: { 'ngrok-skip-browser-warning': 'true' },
        },
      },
    },
  }
})
