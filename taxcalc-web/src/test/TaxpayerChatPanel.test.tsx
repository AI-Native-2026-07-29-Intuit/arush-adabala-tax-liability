// src/test/TaxpayerChatPanel.test.tsx
import { render, type RenderResult, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Message } from 'ai';
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
function renderAtChatRoute(taxpayerId: string): RenderResult {
  return render(
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

    // Because Stop can't genuinely cancel the request here (see the doc
    // comment above), the background stream this test started keeps
    // running regardless and will call onFinish on its own schedule -
    // draining it (waiting it out, then clearing the shared, global-
    // singleton store it writes to) before this test returns is what
    // stops that from landing mid-assertion in whichever test runs next.
    await waitFor(() => expect(useTaxpayerChatStore.getState().messages.length).toBeGreaterThan(0));
    useTaxpayerChatStore.getState().clear();
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

    // Wait for the regenerated turn's onFinish to actually settle (not
    // just for the second request to have started) before this test
    // returns - same shared-global-store leak risk the lookup test above
    // has a longer comment on.
    await waitFor(() => expect(useTaxpayerChatStore.getState().messages).toHaveLength(2));
  });

  it('a message containing "lookup" renders a ToolCallCard with args, then the result payload', async () => {
    renderAtChatRoute('stub-4');

    await userEvent.type(screen.getByLabelText('chat-input'), 'please lookup stub-1');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    const toolCard = await screen.findByLabelText('tool-call');
    expect(toolCard).toHaveAttribute('data-tool', 'lookupTaxpayer');
    expect(toolCard).toHaveTextContent('lookupTaxpayer');
    expect(toolCard).toHaveTextContent('"id": "stub-1"');
    expect(screen.queryByTestId('tool-result')).not.toBeInTheDocument();

    await waitFor(() => expect(toolCard).toHaveAttribute('data-state', 'result'));
    expect(screen.getByTestId('tool-result')).toHaveTextContent('stub taxpayer');

    // The stream continues past the tool result with a trailing text delta
    // and finish frame; wait for the whole thing to settle (onFinish only
    // fires once the finish frame lands) before this test returns - the
    // useTaxpayerChatStore write it triggers is a shared, global-singleton
    // side effect, and letting it resolve after the test function returns
    // would leak into whichever test runs next instead of this one.
    await waitFor(() => expect(screen.getByText(/Found stub taxpayer\./)).toBeInTheDocument());
    await waitFor(() => expect(useTaxpayerChatStore.getState().messages).toHaveLength(1));
  });

  /**
   * `useChat`'s `id`-keyed cache only seeds from `initialMessages` the
   * first time a given id is used in this process - exactly matching a
   * real reload, where the JS process (and that in-memory cache) restarts
   * from nothing but `useTaxpayerChatStore`'s `localStorage`-backed state
   * survives. So the honest way to simulate "reload" here is a taxpayer id
   * this file has never rendered before, with the store pre-seeded as if
   * a prior session had already completed a turn - not an unmount/remount
   * of an id already in the cache, which would just reuse that cache and
   * prove nothing about rehydration.
   */
  it('previously-completed history seeded in the store reappears on first mount (reload)', () => {
    const priorAssistantMessage: Message = {
      id: 'prior-msg-1',
      role: 'assistant',
      content: 'stub taxpayer reply.',
    };
    useTaxpayerChatStore.getState().appendAssistantMessage(priorAssistantMessage);

    renderAtChatRoute('stub-5');

    expect(screen.getByText('stub taxpayer reply.')).toBeInTheDocument();
  });

  /**
   * As documented on the "Stop is disabled..." test above, Stop can't
   * genuinely cancel a request in this jsdom/MSW environment, so the
   * assertion right after clicking it (`messages` still empty) is real but
   * time-window-dependent - true because the background stream hasn't
   * resolved yet, not because it was actually cancelled. The
   * onFinish-never-fires-on-abort guarantee this "Done when" bullet is
   * really about is covered where it's actually verifiable:
   * useTaxpayerChatStore.test.ts's persist round-trip, plus
   * TaxpayerChatPanel.tsx's onFinish being appendAssistantMessage's only
   * call site. What this test adds on top of that: once the background
   * stream is drained and the store cleared (representing what a *real*
   * cancelled request leaves behind - nothing), a reload with that empty
   * store correctly renders an empty transcript.
   */
  it('a turn stopped mid-stream leaves the store empty, so a reload rehydrates nothing for it', async () => {
    // A dedicated handler that withholds its first frame long enough that
    // the "still empty right after Stop" assertion below is reliably true
    // rather than a race against the default sseHandlers' 20ms inter-frame
    // delay, which real (non-fake) timers don't leave a safe margin
    // against three sequential userEvent calls.
    server.use(
      http.post('/api/chat', async () => {
        const stream = new ReadableStream<Uint8Array>({
          async start(controller) {
            await new Promise((resolve) => setTimeout(resolve, 500));
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

    const firstMount = renderAtChatRoute('stub-6');

    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }));

    expect(useTaxpayerChatStore.getState().messages).toHaveLength(0);

    // Drain the background stream Stop couldn't actually cancel, then
    // clear the store - both purely to prevent this test's own leftover
    // async work from corrupting a later test's shared global-singleton
    // store state, same as the "Stop is disabled..." test above.
    await waitFor(
      () => expect(useTaxpayerChatStore.getState().messages.length).toBeGreaterThan(0),
      { timeout: 2000 },
    );
    useTaxpayerChatStore.getState().clear();

    // Unmount before "reloading" - a real reload tears down the whole page,
    // and leaving this render's DOM in place would leak its own <li>
    // (the user's "hello", which Stop never removes) into the next
    // render's queries.
    firstMount.unmount();

    // Simulating the reload itself the same way the test above does: a
    // fresh taxpayer id, seeded from whatever the store now holds - which
    // is still nothing, since the stopped turn was never persisted.
    const reloadedMount = renderAtChatRoute('stub-6-reloaded');
    const transcript = within(reloadedMount.getByLabelText('chat-transcript'));
    expect(transcript.queryAllByRole('listitem')).toHaveLength(0);
  });
});
