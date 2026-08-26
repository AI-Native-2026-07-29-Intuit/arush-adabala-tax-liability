// src/test/chat.test.ts
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { chat } from '../../server/api/chat';
import { server } from './server';

/**
 * Exercises `chat.ts`'s request-body validation directly against the Hono
 * app object via its built-in `.request()` test helper - no real listener
 * needed, and no upstream call happens for any of the rejected cases below
 * since validation short-circuits before `streamText` is ever reached.
 */
describe('chat route request validation', () => {
  it('returns 400 for a body with no messages field at all', async () => {
    const res = await chat.request('/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as { error: string };
    expect(body.error).toMatch(/invalid request body/i);
  });

  it('returns 400 for an empty messages array', async () => {
    const res = await chat.request('/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: [] }),
    });
    expect(res.status).toBe(400);
  });

  it('returns 400 for a message with no valid role', async () => {
    const res = await chat.request('/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: [{ content: 'hello' }] }),
    });
    expect(res.status).toBe(400);
  });

  it('returns 400 for malformed JSON rather than throwing', async () => {
    const res = await chat.request('/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: 'not valid json',
    });
    expect(res.status).toBe(400);
  });

  it('proceeds past validation for a well-formed body, reaching the upstream call', async () => {
    server.use(
      http.post('http://localhost:8080/ai/chat/completions', () => {
        const encoder = new TextEncoder();
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n',
              ),
            );
            controller.enqueue(
              encoder.encode('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'),
            );
            controller.enqueue(encoder.encode('data: [DONE]\n\n'));
            controller.close();
          },
        });
        return new HttpResponse(stream, { headers: { 'Content-Type': 'text/event-stream' } });
      }),
    );

    const res = await chat.request('/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: [{ id: '1', role: 'user', content: 'hello' }] }),
    });
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/event-stream');
  });
});
