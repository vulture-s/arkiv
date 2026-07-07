import { defineConfig, loadEnv } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// arkiv frontend — Svelte 4 + Vite 5. Backend (FastAPI) runs on :8501;
// dev proxy forwards /api + /thumbnails so the SPA can call it during `npm run dev`.
export default defineConfig(({ mode }) => {
  // Extra dev-server Host-header allowlist, sourced from the environment so it stays
  // out of git. Vite blocks non-localhost Host headers (anti DNS-rebinding); to view
  // the dev server over e.g. Tailscale serve, set in a gitignored frontend/.env.local:
  //   ARKIV_DEV_ALLOWED_HOSTS=.ts.net
  // Leading-dot entries match a domain and all its subdomains. Empty = Vite default.
  // Read via loadEnv (Vite does NOT populate process.env from .env files); the
  // non-VITE_ prefix keeps it server-side only, out of the client bundle.
  const env = loadEnv(mode, process.cwd(), '')
  const extraAllowedHosts = (env.ARKIV_DEV_ALLOWED_HOSTS ?? '')
    .split(',')
    .map((h) => h.trim())
    .filter(Boolean)

  return {
    plugins: [svelte()],
    server: {
      port: 5173,
      allowedHosts: extraAllowedHosts,
      proxy: {
        // explicit IPv4 — `localhost` resolves to ::1 first on dual-stack macOS,
        // but the backend binds 127.0.0.1, so localhost would ECONNREFUSED.
        '/api': 'http://127.0.0.1:8501',
        '/thumbnails': 'http://127.0.0.1:8501',
        '/ws': { target: 'ws://127.0.0.1:8501', ws: true },
      },
    },
  }
})
