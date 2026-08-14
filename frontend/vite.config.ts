import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The app calls the FastAPI backend via relative paths (e.g. fetch("/admin/agents"))
// everywhere, so the exact same code works in dev (proxied below) and in
// production (served from the same FastAPI origin as a static build, see
// backend/main.py's StaticFiles mount).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    // Vite rejects requests whose Host header it doesn't recognize (guards
    // against DNS-rebinding attacks). ngrok's tunnel domain isn't localhost
    // or the LAN IP, so without this every request gets a 403 "Blocked
    // request. This host is not allowed." True is fine for a demo tunnel
    // that changes URL every run; pin it to a specific hostname instead if
    // you get a reserved ngrok domain.
    allowedHosts: true,
    proxy: {
      '/admin': 'http://127.0.0.1:8080',
      '/agents': 'http://127.0.0.1:8080',
      // Inline calculators (EMI, FIRE). Without this the Playground's
      // calculator silently gets Vite's index.html back instead of a result.
      '/api': 'http://127.0.0.1:8080',
      '/workflows': 'http://127.0.0.1:8080',
      '/runs': 'http://127.0.0.1:8080',
      '/healthz': 'http://127.0.0.1:8080',
    },
  },
})
