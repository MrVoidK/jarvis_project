import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // core/api.py:_ALLOWED_ORIGINS ile AYNI host/port - backend'in CORS/
    // WebSocket Origin kontrolu sadece bu adresi kabul ediyor.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})
