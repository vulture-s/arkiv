import './app.css'

// Self-hosted fonts (offline after `npm install`; replaces the mockup's
// Google Fonts CDN @import — required under Tauri CSP later).
import '@fontsource/archivo-black/400.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/700.css'
import '@fontsource/noto-sans-tc/400.css'
import '@fontsource/noto-sans-tc/500.css'
import '@fontsource/noto-sans-tc/700.css'

import App from './App.svelte'

const app = new App({ target: document.getElementById('app') })

export default app
