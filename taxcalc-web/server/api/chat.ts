// server/api/chat.ts
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { streamText } from 'ai';
import { Hono } from 'hono';
import { taxpayerTools } from './chat-tools';

/** Maximum sequential LLM calls per turn: one tool call plus its follow-up reply. */
const MAX_STEPS = 3;

/**
 * THREAT MODEL: this proxy holds the upstream API key (via
 * `createOpenAICompatible`'s config, not hard-coded here). The browser
 * never sees it - the proxy receives only message history from the
 * authenticated W4 D3 protected layout, never raw secrets, and only ever
 * emits `text/event-stream` chunks back. `spring-ai` targets the W3 D4
 * backend's OpenAI-compatible chat endpoint rather than a real provider,
 * so this proxy never touches Anthropic's or OpenAI's actual API.
 */
const upstream = createOpenAICompatible({
  name: 'spring-ai',
  baseURL: 'http://localhost:8080/ai',
});

const SYSTEM_PROMPT =
  "You are an assistant that helps engineers reason about a taxpayer's federal and state tax " +
  'liability. Answer from the conversation and your own knowledge by default. When a question ' +
  "needs a specific taxpayer's current record, call lookupTaxpayer with that taxpayer's id. " +
  'When a question needs a fresh liability figure for a given tax year, call estimateLiability ' +
  'with that year. Never fabricate an id, amount, or filing status - call the matching tool ' +
  "instead of guessing, and if a tool call fails, say so rather than inventing a result.";

/**
 * Maps any failure from the upstream Spring AI call - connection refused
 * because the container isn't running, a 4xx/5xx status, a malformed
 * response - onto one client-safe SSE error frame instead of a torn
 * connection. `toDataStreamResponse` already catches stream-time failures
 * and emits an error data-stream chunk (`useChat`'s `error` field), but its
 * default `getErrorMessage` returns the generic "An error occurred." and
 * swallows the real cause; this logs that cause server-side and returns a
 * message that's actually useful in the `role="alert"` pane the client
 * renders it in.
 */
function toClientErrorMessage(error: unknown): string {
  console.error('chat proxy: upstream call failed', error);
  return 'The tax assistant is temporarily unavailable. Please try again in a moment.';
}

/**
 * `POST /api/chat` (mounted at that prefix by `server/index.ts`; this
 * sub-app itself only defines `/`). Reads `{ messages }` from the request
 * body, streams the model's reply back as Server-Sent Events via
 * {@link streamText}'s {@link https://sdk.vercel.ai/docs data-stream
 * protocol}, and forwards the incoming request's `AbortSignal` so a client
 * disconnect (the browser's Stop button, or a closed tab) cancels the
 * upstream LLM call too instead of leaking it. The explicit
 * `text/event-stream` / `no-cache, no-transform` / `X-Accel-Buffering: no`
 * headers exist because a reverse proxy or CDN in front of this route
 * would otherwise buffer the whole response before forwarding it, turning
 * token-by-token streaming into one final chunk on the wire.
 */
export const chat = new Hono().post('/', async (c) => {
  const { messages } = await c.req.json();

  const result = streamText({
    model: upstream.chatModel('uptime-crew-assistant'),
    system: SYSTEM_PROMPT,
    messages,
    tools: taxpayerTools,
    maxSteps: MAX_STEPS,
    abortSignal: c.req.raw.signal,
  });

  return result.toDataStreamResponse({
    getErrorMessage: toClientErrorMessage,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
});
