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

  // Where the app is mounted. '/' for local dev; a deployment behind a
  // sub-path sets APP_BASE=/onboarding-v2/ (leading and trailing slash).
  //
  // This has to reach three places at once or the app half-works: Vite's own
  // `base` (so built asset URLs resolve), import.meta.env.BASE_URL (which
  // src/lib/basePath.js builds every same-origin URL from), and the proxy
  // routes below -- the browser will now ask for `/onboarding-v2/tts-ws`, so a
  // proxy key of `/tts-ws` would never match and the socket would hang at
  // "Connecting…" exactly as if the route did not exist.
  const rawBase = env.APP_BASE || '/'
  const base = rawBase.endsWith('/') ? rawBase : `${rawBase}/`
  // Named `mount`, not `p`: the rewrite callbacks below take the path as `p`,
  // and a one-letter helper here would be shadowed inside exactly the place
  // that needs it.
  const mount = (route) => `${base.replace(/\/+$/, '')}${route}`   // '/voice-api' -> '/onboarding-v2/voice-api'
  // Strips the app base AND the route prefix, so the upstream sees the path it
  // expects regardless of where this app is mounted.
  const strip = (route) => (path) => path.replace(new RegExp(`^${mount(route)}`), '')
  // Strips ONLY the app base, keeping the route. For upstreams that own the
  // route name themselves (/agents/..., /api/tools/...) rather than having it
  // rewritten away. Without this the backend receives
  // "/onboarding-v2/agents/finguru/invoke" and answers 404 -- which reads as a
  // broken backend rather than a mis-proxied path.
  const stripBase = (path) => path.replace(new RegExp(`^${base.replace(/\/+$/, '')}`), '') || '/'

  return {
    base,
    plugins: [react()],
    server: {
      port: frontendPort,
      // Vite rejects any request whose Host header it does not recognise, so
      // reaching this dev server through a tunnel returns "Blocked request.
      // This host is not allowed" for the page AND every proxied call. The
      // check is a DNS-rebinding protection and is worth keeping narrow:
      // this lists the tunnel domains rather than disabling it with `true`.
      // Add a domain here (or set FRONTEND_ALLOWED_HOSTS=a,b) when a new
      // tunnel is used.
      allowedHosts: [
        '.ngrok-free.dev',
        '.ngrok.io',
        '.trycloudflare.com',
        '.innohub.sbi',          // sandbox/demo hosting
        'localhost',
        ...(env.FRONTEND_ALLOWED_HOSTS
          ? env.FRONTEND_ALLOWED_HOSTS.split(',').map((h) => h.trim()).filter(Boolean)
          : []),
      ],
      // Same-origin proxy to the reference voice server. The browser calls
      // /voice-api/* (no CORS/preflight); Vite forwards to <target>/voice/*
      // with the Bearer token attached here.
      proxy: {
        // FinGuru's own agent backend, same-origin. Added so the app works
        // when this dev server is reached from somewhere other than this
        // machine -- over a tunnel, or from a phone on the LAN. With
        // VITE_FINGURU_URL pointing at http://localhost:8080 the browser
        // resolves "localhost" to ITS OWN machine, so every request fails for
        // anyone but the developer; and an https tunnel serving a page that
        // calls http is blocked as mixed content regardless.
        //
        // Set VITE_FINGURU_URL=/agents/finguru/invoke to use this. An
        // absolute URL still works and bypasses this entirely, so nothing
        // that already points at a hosted backend changes.
        [mount('/agents')]: {
          target: env.FINGURU_BACKEND_TARGET || 'http://localhost:8080',
          changeOrigin: true,
          secure: false,
          rewrite: stripBase,
        },
        // Same-origin path for the name-identity + dynamic-tools endpoints on
        // that same backend: /api/history, /api/tools, /api/tools/execute,
        // /api/tools/save, /api/tools/saved.
        //
        // Keyed on '/api', not '/api/tools': proxy keys are prefix matches, so
        // the narrower key never matched /api/history. That request fell
        // through to the SPA and came back as index.html with a 200 -- the
        // client then tried to parse HTML as JSON. The onboarding API is
        // reached through VITE_API_BASE (an absolute URL, different port), so
        // nothing else competes for this prefix.
        [mount('/api')]: {
          target: env.FINGURU_BACKEND_TARGET || 'http://localhost:8080',
          changeOrigin: true,
          secure: false,
          rewrite: stripBase,
        },
        [mount('/voice-api')]: {
          target: voiceTarget,
          changeOrigin: true,
          secure: true,
          rewrite: (path) => `/voice${strip('/voice-api')(path)}`,
          headers: {
            'ngrok-skip-browser-warning': 'true',
            ...(voiceKey ? { Authorization: `Bearer ${voiceKey}` } : {}),
          },
        },
        // Streaming-TTS WebSocket for the Ollama-fallback voice path -- the
        // voice server's own local (Parler-TTS) streaming synth, never Sarvam.
        [mount('/tts-ws')]: {
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
        [mount('/sarvam-api')]: {
          target: sarvamTarget,
          changeOrigin: true,
          secure: true,
          rewrite: strip('/sarvam-api'),
          headers: sarvamKey ? { 'api-subscription-key': sarvamKey } : {},
        },
        // Sarvam's realtime STT WebSocket (saaras:v3-realtime), used for live
        // calls. The browser opens same-origin ws://<host>/sarvam-stt-ws?<query>;
        // Vite upgrades and proxies straight to Sarvam with the subscription
        // key attached server-side (browsers can't set custom WS handshake
        // headers, so this proxy is what keeps the key out of the client).
        [mount('/sarvam-stt-ws')]: {
          target: sarvamTarget,
          changeOrigin: true,
          secure: true,
          ws: true,
          rewrite: (path) => `/speech-to-text-realtime/ws${strip('/sarvam-stt-ws')(path)}`,
          headers: sarvamKey ? { 'api-subscription-key': sarvamKey } : {},
        },
      },
    },
  }
})
