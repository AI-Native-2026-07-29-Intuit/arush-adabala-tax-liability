// server/index.ts
import { serve } from '@hono/node-server';
import { Hono } from 'hono';
import { chat } from './api/chat';

const PORT = 3001;

/**
 * Thin Hono entry point for the W4 D4 streaming proxy. Runs as a sibling
 * process to the Vite dev server (`pnpm server`, alongside `pnpm dev`);
 * `vite.config.ts`'s `server.proxy` forwards the browser's `/api/chat`
 * requests here so the client never needs its own base URL for this host.
 * Kept to one route today - `chat` in `./api/chat.ts` - because that's the
 * only endpoint this deliverable needs.
 */
const app = new Hono();
app.route('/api/chat', chat);

serve({ fetch: app.fetch, port: PORT });

console.log(`taxcalc-web chat proxy listening on http://localhost:${PORT}`);
