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
// A real 200 for the Playwright webServer readiness probe (playwright.config.ts) -
// Playwright's `url` check only considers the server started once it sees a
// response under 400, and this app otherwise has no route at `/` (only
// `POST /api/chat`), which 404s and would leave the check waiting forever.
app.get('/health', (c) => c.text('ok'));
app.route('/api/chat', chat);

serve({ fetch: app.fetch, port: PORT });

console.log(`taxcalc-web chat proxy listening on http://localhost:${PORT}`);
