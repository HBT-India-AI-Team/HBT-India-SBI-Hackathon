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
    proxy: {
      '/admin': 'http://127.0.0.1:8080',
      '/agents': 'http://127.0.0.1:8080',
      '/workflows': 'http://127.0.0.1:8080',
      '/runs': 'http://127.0.0.1:8080',
      '/healthz': 'http://127.0.0.1:8080',
    },
  },
})
