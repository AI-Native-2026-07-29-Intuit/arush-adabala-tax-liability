// src/test/TaxpayerChatPanel.test.tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { TaxpayerChatPanel } from '../pages/TaxpayerChatPanel';
import { useTaxpayerChatStore } from '../stores/useTaxpayerChatStore';
import { server } from './server';

/**
 * `useChat`'s internal `id`-keyed cache is a module-global SWR store, not
 * component-local state - it outlives `render()`/unmount across tests in
 * the same file. Each test renders at its own taxpayer id so its
 * `taxpayer-${id}` chat cache starts empty instead of inheriting messages
 * a previous test left behind.
 */
function renderAtChatRoute(taxpayerId: string): void {
  render(
    <MemoryRouter initialEntries={[`/taxpayers/${taxpayerId}/chat`]}>
      <Routes>
        <Route path="/taxpayers/:id/chat" element={<TaxpayerChatPanel />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // Resetting via setState (not window.localStorage.clear() - it's
  // undefined under this Node/jsdom/Vitest combination, see
  // src/lib/safeLocalStorage.ts) also re-triggers the persist middleware's
  // own write, so the storage layer resets along with the live state.
  useTaxpayerChatStore.setState(useTaxpayerChatStore.getInitialState(), true);
});

describe('TaxpayerChatPanel', () => {
  it('streams the stub assistant reply token-by-token, then persists it on finish', async () => {
    renderAtChatRoute('stub-1');

    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(screen.getByRole('status')).toHaveTextContent('Assistant is replying...');
    await waitFor(() => expect(screen.getByText(/stub taxpayer reply\./)).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());

    await waitFor(() => expect(useTaxpayerChatStore.getState().messages).toHaveLength(1));
    expect(useTaxpayerChatStore.getState().messages[0]?.content).toBe('stub taxpayer reply.');
  });

  /**
   * Genuinely verifying that Stop interrupts an in-flight network stream
   * isn't possible under this Node/jsdom/MSW combination: jsdom's
   * AbortController/AbortSignal are a separate, JS-implemented class from
   * the one Node's native `Request` constructor validates `init.signal`
   * against, and once jsdom's test environment overwrites
   * `globalThis.AbortController`, there is no way to recover Node's
   * original class reference from test code - which is exactly why
   * `test/server.ts`'s `beforeAll` strips `init.signal` from every fetch
   * call before it reaches MSW's interceptor (otherwise MSW rejects it
   * outright with "Expected signal to be an instance of AbortSignal").
   * With the signal never reaching the real fetch call, aborting can't
   * interrupt an already-issued request in this test environment - a
   * test-infra gap, not a product bug (a real browser has one
   * AbortController class, so this problem doesn't exist there).
   *
   * What's still genuinely verifiable here: the button wiring itself
   * (disabled until a request is in flight, calling `stop()` doesn't
   * throw), and - at the unit level, in useTaxpayerChatStore.test.ts plus
   * by inspection of TaxpayerChatPanel.tsx - that `appendAssistantMessage`
   * has exactly one call site, `onFinish`, which the ui-utils source
   * confirms never fires for an aborted request.
   */
  it('Stop is disabled until a request is in flight, and clicking it does not throw', async () => {
    renderAtChatRoute('stub-2');
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled();

    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(screen.getByRole('button', { name: 'Stop' })).not.toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: 'Stop' }));
  });

  it('Regenerate re-submits and fires a second POST to /api/chat', async () => {
    let requestCount = 0;
    server.use(
      http.post('/api/chat', () => {
        requestCount += 1;
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('0:"stub taxpayer reply."\n'));
            controller.enqueue(
              new TextEncoder().encode(
                'd:{"finishReason":"stop","usage":{"promptTokens":1,"completionTokens":3}}\n',
              ),
            );
            controller.close();
          },
        });
        return new HttpResponse(stream, {
          headers: { 'Content-Type': 'text/event-stream', 'X-Vercel-AI-Data-Stream': 'v1' },
        });
      }),
    );

    renderAtChatRoute('stub-3');
    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(requestCount).toBe(1));
    await waitFor(() => expect(screen.getByText(/stub taxpayer reply\./)).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    await waitFor(() => expect(requestCount).toBe(2));
  });
});
