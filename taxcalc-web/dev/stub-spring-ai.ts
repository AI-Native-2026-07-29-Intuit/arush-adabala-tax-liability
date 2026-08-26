// dev/stub-spring-ai.ts
import { serve } from '@hono/node-server';
import { Hono } from 'hono';

const PORT = 8080;

/**
 * A minimal, hand-rolled stand-in for the W3 D4 Spring AI backend (and the
 * slice of the W3 D2 REST backend the chat tools call) - NOT a real
 * implementation of either. Neither exists anywhere in this repo's git
 * history: there's no docker-compose for the Spring AI container, and
 * `TaxpayerController` only has `GET /{id}`, not the `?year=` list endpoint
 * `estimateLiability` targets. Without something listening on :8080, the
 * chat proxy's "Done when" checks that ask to watch real tokens stream in
 * can only ever be verified through curl, a bare-HTTP stub, or MSW mocks -
 * never through the actual browser UI. This exists purely to make that
 * happy path demonstrable locally: `pnpm dev` + `pnpm server` + `pnpm
 * stub-backend`, then drive the UI in a real browser and watch it work.
 *
 * Speaks the real OpenAI-compatible chat-completions wire format (not the
 * Vercel data-stream protocol `server/api/chat.ts` emits to the browser -
 * this is one level further upstream, where `@ai-sdk/openai-compatible`
 * itself is the client) so `streamText`'s actual parsing path is exercised
 * end-to-end, tool-call orchestration included, rather than bypassed.
 */
const app = new Hono();

interface IncomingMessage {
  readonly role: string;
  readonly content?: string;
}

interface ChatCompletionsRequestBody {
  readonly messages?: readonly IncomingMessage[];
}

const encoder = new TextEncoder();

function sseChunk(payload: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(payload)}\n\n`);
}

const DONE = encoder.encode('data: [DONE]\n\n');

function baseChunk(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    id: 'chatcmpl-stub',
    object: 'chat.completion.chunk',
    created: Math.floor(Date.now() / 1000),
    model: 'uptime-crew-assistant',
    ...overrides,
  };
}

/**
 * `@ai-sdk/openai-compatible`'s `chatModel()` posts here
 * (`{baseURL}/chat/completions`, matching `server/api/chat.ts`'s
 * `baseURL: 'http://localhost:8080/ai'`). A request already carrying a
 * `role: "tool"` message is step two of a tool-calling exchange (the
 * client executed `lookupTaxpayer`/`estimateLiability` itself and is
 * asking for the follow-up reply); a fresh request whose last user message
 * mentions "lookup" triggers a canned tool call instead of a plain reply -
 * mirroring `src/test/sse-handlers.ts`'s branch, but through the real
 * OpenAI-compatible stream format this time, not the Vercel one.
 */
app.post('/ai/chat/completions', async (c) => {
  const body = (await c.req.json()) as ChatCompletionsRequestBody;
  const messages = body.messages ?? [];
  const hasToolResult = messages.some((m) => m.role === 'tool');
  const lastUserContent = [...messages].reverse().find((m) => m.role === 'user')?.content ?? '';
  const wantsToolCall = !hasToolResult && lastUserContent.toLowerCase().includes('lookup');

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      if (wantsToolCall) {
        controller.enqueue(
          sseChunk(
            baseChunk({
              choices: [
                {
                  index: 0,
                  delta: {
                    tool_calls: [
                      {
                        index: 0,
                        id: 'call_stub_1',
                        type: 'function',
                        function: { name: 'lookupTaxpayer', arguments: '{"id":"stub-1"}' },
                      },
                    ],
                  },
                  finish_reason: null,
                },
              ],
            }),
          ),
        );
        controller.enqueue(
          sseChunk(
            baseChunk({ choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }] }),
          ),
        );
      } else if (hasToolResult) {
        for (const word of ['Found ', 'stub ', 'taxpayer ', 'stub-1.']) {
          controller.enqueue(
            sseChunk(baseChunk({ choices: [{ index: 0, delta: { content: word }, finish_reason: null }] })),
          );
          await new Promise((resolve) => setTimeout(resolve, 60));
        }
        controller.enqueue(
          sseChunk(baseChunk({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })),
        );
      } else {
        for (const word of ['Hello ', 'from ', 'the ', 'stub ', 'tax ', 'assistant.']) {
          controller.enqueue(
            sseChunk(baseChunk({ choices: [{ index: 0, delta: { content: word }, finish_reason: null }] })),
          );
          await new Promise((resolve) => setTimeout(resolve, 60));
        }
        controller.enqueue(
          sseChunk(baseChunk({ choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })),
        );
      }
      controller.enqueue(DONE);
      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
    },
  });
});

/**
 * Canned stand-ins for the two REST calls `server/api/chat-tools.ts`
 * makes. `lookupTaxpayer` hits an endpoint that's real on the actual W3 D2
 * backend; `estimateLiability`'s `?year=` list endpoint isn't implemented
 * there at all (`TaxpayerController` only has `GET /{id}`) - both are
 * stubbed here identically so the tool-call demo doesn't depend on which
 * of the two gaps applies.
 */
app.get('/api/v1/taxpayers/:id', (c) => {
  const id = c.req.param('id');
  return c.json({
    id,
    displayName: 'Stub Taxpayer',
    filingStatus: 'SINGLE',
    homeJurisdiction: 'COLORADO',
    createdAt: '2025-01-04T00:00:00Z',
    liabilities: [],
    tags: [],
  });
});

app.get('/api/v1/taxpayers', (c) => {
  const year = c.req.query('year');
  return c.json([
    { id: 'stub-1', taxYear: Number(year ?? 2024), liabilityAmount: 4820 },
    { id: 'stub-2', taxYear: Number(year ?? 2024), liabilityAmount: 9310 },
  ]);
});

serve({ fetch: app.fetch, port: PORT });

console.log(`stub Spring AI + REST backend listening on http://localhost:${PORT}`);
