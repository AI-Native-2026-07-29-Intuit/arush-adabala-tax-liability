// server/api/chat.ts
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { streamText } from 'ai';
import { Hono } from 'hono';
import { taxpayerTools } from './chat-tools';

/** Maximum sequential LLM calls per turn: one tool call plus its follow-up reply. */
const MAX_STEPS = 3;

const GENERIC_CLIENT_MESSAGE =
  'The tax assistant is temporarily unavailable. Please try again in a moment.';

/**
 * Thrown by {@link mapUpstreamErrors} when the W3 D4 Spring AI backend
 * responds with a 4xx/5xx status. Carries a message already safe to show a
 * user, so {@link toClientErrorMessage} can use it verbatim instead of
 * falling back to the generic one - a mapped upstream failure still
 * produces a deliberate, specific sentinel frame rather than losing that
 * distinction once it's caught downstream.
 */
class UpstreamStatusError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'UpstreamStatusError';
  }
}

/**
 * The 5xx-mapping middleware: `createOpenAICompatible`'s own `fetch`
 * option is documented as exactly this - "a custom fetch implementation
 * you can use as a middleware to intercept requests" - wrapping every
 * outbound call this proxy makes to the Spring AI backend. A 4xx/5xx
 * response carries a JSON error body, not a chat-completion stream; handing
 * that straight to the AI SDK's stream decoder would surface as an opaque
 * parse failure deep inside the SDK instead of a clean, attributable
 * error. Mapping the status here - before the SDK ever sees the body -
 * turns it into one well-typed {@link UpstreamStatusError} up front.
 * Network-level failures (connection refused, DNS, timeout) never reach
 * this function at all, since the `fetch()` call itself rejects before
 * returning a `Response` to inspect - those fall through unchanged to
 * `streamText`'s own retry/error handling instead.
 */
const mapUpstreamErrors: typeof fetch = async (input, init) => {
  const response = await fetch(input, init);
  if (response.status >= 400) {
    const body = await response.text().catch(() => '<unreadable body>');
    console.error(
      `chat proxy: upstream returned ${response.status} ${response.statusText}`,
      body,
    );
    throw new UpstreamStatusError(response.status, GENERIC_CLIENT_MESSAGE);
  }
  return response;
};

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
  fetch: mapUpstreamErrors,
});

const SYSTEM_PROMPT =
  "You are an assistant that helps engineers reason about a taxpayer's federal and state tax " +
  'liability. Answer from the conversation and your own knowledge by default. When a question ' +
  "needs a specific taxpayer's current record, call lookupTaxpayer with that taxpayer's id. " +
  'When a question needs a fresh liability figure for a given tax year, call estimateLiability ' +
  'with that year. Never fabricate an id, amount, or filing status - call the matching tool ' +
  "instead of guessing, and if a tool call fails, say so rather than inventing a result.";

/**
 * The final translation step for any failure from the upstream Spring AI
 * call, whatever its source - a status `mapUpstreamErrors` already mapped
 * to an {@link UpstreamStatusError}, a connection refused because the
 * container isn't running, a malformed response - onto one client-safe SSE
 * error frame instead of a torn connection. `toDataStreamResponse` already
 * catches stream-time failures and emits an error data-stream chunk
 * (`useChat`'s `error` field), but its default `getErrorMessage` returns
 * the generic "An error occurred." and swallows the real cause; this logs
 * that cause server-side either way, and uses the upstream-mapped message
 * verbatim when there is one rather than falling back to the generic
 * message for a failure that was already deliberately classified.
 */
function toClientErrorMessage(error: unknown): string {
  console.error('chat proxy: upstream call failed', error);
  if (error instanceof UpstreamStatusError) return error.message;
  return GENERIC_CLIENT_MESSAGE;
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
