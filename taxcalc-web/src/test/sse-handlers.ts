// src/test/sse-handlers.ts
import { delay, http, HttpResponse } from 'msw';

interface ChatRequestBody {
  readonly messages?: ReadonlyArray<{ readonly content?: string }>;
}

/**
 * One `TextEncoder`, reused for every frame this file emits - encoding is
 * stateless, so there's no reason to allocate a fresh instance per call.
 */
const encoder = new TextEncoder();

/**
 * Encodes one line of the Vercel AI SDK's data-stream protocol
 * (`{prefix}:{JSON}\n`) - `0` for a text delta, `9`/`a` for a tool call and
 * its result, `d` for the terminating finish-message frame. Verified
 * against `@ai-sdk/ui-utils`'s own parser (`processDataStream`'s
 * `onToolCallPart`/`onToolResultPart`/`onFinishMessagePart`) rather than
 * guessed, since a wrong prefix fails silently client-side (the chunk is
 * just never rendered) instead of raising a test error.
 */
function encodeFrame(prefix: string, payload: unknown): Uint8Array {
  const line = `${prefix}:${JSON.stringify(payload)}\n`;
  return encoder.encode(line);
}

const STREAM_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache, no-transform',
  'X-Vercel-AI-Data-Stream': 'v1',
};

const FINISH_FRAME = { finishReason: 'stop', usage: { promptTokens: 1, completionTokens: 3 } };

/**
 * MSW handlers standing in for `server/api/chat.ts` under Vitest - no Hono
 * process, no upstream Spring AI backend, just a hand-rolled
 * `ReadableStream` emitting the same wire format `streamText` +
 * `toDataStreamResponse` would. A short `delay()` between frames gives
 * tests exercising Stop-mid-stream a real window to click before the
 * stream finishes; the reply branches on whether the request's last user
 * message contains "lookup", so one handler covers both the plain-text
 * streaming tests and the tool-call test without a second endpoint.
 */
export const sseHandlers = [
  http.post('/api/chat', async ({ request }) => {
    const body = (await request.json()) as ChatRequestBody;
    const lastUserContent = body.messages?.at(-1)?.content ?? '';
    const wantsToolCall = lastUserContent.includes('lookup');

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        if (wantsToolCall) {
          controller.enqueue(
            encodeFrame('9', {
              toolCallId: 'call-1',
              toolName: 'lookupTaxpayer',
              args: { id: 'stub-1' },
            }),
          );
          await delay(20);
          controller.enqueue(
            encodeFrame('a', {
              toolCallId: 'call-1',
              result: { id: 'stub-1', displayName: 'stub taxpayer' },
            }),
          );
          await delay(20);
          controller.enqueue(encodeFrame('0', 'Found stub taxpayer.'));
        } else {
          controller.enqueue(encodeFrame('0', 'stub '));
          await delay(20);
          controller.enqueue(encodeFrame('0', 'taxpayer '));
          await delay(20);
          controller.enqueue(encodeFrame('0', 'reply.'));
        }
        await delay(20);
        controller.enqueue(encodeFrame('d', FINISH_FRAME));
        controller.close();
      },
    });

    return new HttpResponse(stream, { headers: STREAM_HEADERS });
  }),
];
