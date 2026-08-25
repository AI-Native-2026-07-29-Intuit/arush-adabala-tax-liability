import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forwards the browser's /api/chat SSE requests to the Hono proxy
      // (`pnpm server`, server/index.ts) so useChat's `api: '/api/chat'`
      // needs no separate base URL in dev. changeOrigin isn't needed -
      // both sides are localhost - but ws:false makes explicit that this
      // is a chunked HTTP/1.1 stream, not a WebSocket upgrade.
      '/api/chat': {
        target: 'http://localhost:3001',
        changeOrigin: false,
        ws: false,
      },
    },
  },
});
