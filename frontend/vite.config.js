import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// arkiv frontend — Svelte 4 + Vite 5. Backend (FastAPI) runs on :8501;
// dev proxy forwards /api + /thumbnails so the SPA can call it during `npm run dev`.
export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8501',
      '/thumbnails': 'http://localhost:8501',
    },
  },
})
