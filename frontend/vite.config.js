import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// arkiv frontend — Svelte 4 + Vite 5. Backend (FastAPI) runs on :8501;
// dev proxy forwards /api + /thumbnails so the SPA can call it during `npm run dev`.
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      // explicit IPv4 — `localhost` resolves to ::1 first on dual-stack macOS,
      // but the backend binds 127.0.0.1, so localhost would ECONNREFUSED.
      '/api': 'http://127.0.0.1:8501',
      '/thumbnails': 'http://127.0.0.1:8501',
    },
  },
})
