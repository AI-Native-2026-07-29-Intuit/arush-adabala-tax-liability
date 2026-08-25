// src/test/TaxpayerChatPanel.test.tsx
import { render, type RenderResult, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Message } from 'ai';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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

    const assistantItem = screen.getByText(/stub taxpayer reply\./).closest('li');
    expect(assistantItem).toHaveAttribute('data-role', 'assistant');

    await waitFor(() => expect(useTaxpayerChatStore.getState().messages).toHaveLength(1));
    expect(useTaxpayerChatStore.getState().messages[0]?.content).toBe('stub taxpayer reply.');
  });

  /**
   * Genuinely verifying that Stop interrupts an in-flight network stream
   * requires the caller's real `AbortSignal` to actually reach the network
   * layer. Under this Node/jsdom/Vitest combination it can't by default:
   * jsdom's `AbortController`/`AbortSignal` are a separate class from the
   * one Node's native `fetch`/`Request` (built on undici) validates
   * `init.signal` against internally via a webidl `instanceof` check
   * against undici's own module-scoped reference - confirmed by reading
   * undici's `webidl/index.js`, not assumed - and vitest's jsdom
   * environment setup hardcodes `AbortController`/`AbortSignal` into the
   * fixed list of globals it copies from `window`, unconditionally
   * overwriting Node's native ones for every test file. `test/server.ts`'s
   * `beforeAll` works around this: it strips the incompatible signal
   * before the real fetch call (avoiding that crash), then reimplements
   * cancellation itself at the response body-stream level, erroring the
   * stream with a plain `Error` named `'AbortError'` once the caller's
   * signal aborts - the only thing `@ai-sdk/provider-utils`'s
   * `isAbortError` actually checks. That's what makes `stop()` genuinely
   * interrupt the stream below, not a timing coincidence.
   */
  it('Stop mid-stream flips isLoading false and freezes the message shorter than the full stub', async () => {
    server.use(
      http.post('/api/chat', async () => {
        const stream = new ReadableStream<Uint8Array>({
          async start(controller) {
            controller.enqueue(new TextEncoder().encode('0:"stub "\n'));
            // Long enough that clicking Stop right after "stub " appears
            // has a comfortable margin before the next frame would arrive.
            await new Promise((resolve) => setTimeout(resolve, 500));
            controller.enqueue(new TextEncoder().encode('0:"taxpayer "\n'));
            controller.enqueue(new TextEncoder().encode('0:"reply."\n'));
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

    renderAtChatRoute('stub-2');
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled();

    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(screen.getByRole('button', { name: 'Stop' })).not.toBeDisabled();

    await waitFor(() => expect(screen.getByText('stub')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }));

    // isLoading flips false.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled());
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    // The message text is shorter than the full stub reply - frozen at
    // whatever had streamed in by the time Stop fired.
    const assistantItem = screen.getByText(/stub/).closest('li');
    expect(assistantItem).toHaveAttribute('data-role', 'assistant');
    const fullStubReply = 'stub taxpayer reply.';
    expect(assistantItem?.textContent).not.toContain('reply.');
    expect(assistantItem?.textContent?.length ?? Infinity).toBeLessThan(fullStubReply.length);

    // onFinish never fires for a genuinely aborted request, so nothing was
    // persisted for this turn - confirmed by waiting past the point where
    // the un-aborted continuation would have arrived (the 500ms delay
    // above) and finding the store still empty, not just checking
    // immediately after Stop.
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(useTaxpayerChatStore.getState().messages).toHaveLength(0);
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

  it('a turn stopped mid-stream leaves the store empty, so a reload rehydrates nothing for it', async () => {
    // A dedicated handler that withholds its first frame long enough to
    // click Stop with a comfortable margin before any content would
    // otherwise arrive.
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

    // Stop now genuinely cancels the request (see test/server.ts), so the
    // withheld frame above never arrives and onFinish never fires -
    // waiting out that same 500ms window and re-checking confirms nothing
    // shows up later either, not just that the store hadn't been written
    // to yet at the moment Stop was clicked.
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(useTaxpayerChatStore.getState().messages).toHaveLength(0);

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

  it('Send is disabled when the input is empty or whitespace-only', async () => {
    renderAtChatRoute('stub-7');
    const input = screen.getByLabelText('chat-input');
    const sendButton = screen.getByRole('button', { name: 'Send' });

    expect(sendButton).toBeDisabled();

    await userEvent.type(input, '   ');
    expect(sendButton).toBeDisabled();

    await userEvent.type(input, 'hi');
    expect(sendButton).not.toBeDisabled();
  });

  it('Regenerate is disabled while a request is in flight', async () => {
    renderAtChatRoute('stub-8');
    const regenerateButton = screen.getByRole('button', { name: 'Regenerate' });
    expect(regenerateButton).not.toBeDisabled();

    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(regenerateButton).toBeDisabled();

    await waitFor(() => expect(screen.getByText(/stub taxpayer reply\./)).toBeInTheDocument());
    // isLoading only flips false once the finish frame is processed, a
    // moment after the last text frame renders - wait for that instead of
    // checking immediately after the text appears.
    await waitFor(() => expect(regenerateButton).not.toBeDisabled());
    await waitFor(() => expect(useTaxpayerChatStore.getState().messages).toHaveLength(1));
  });

  it('auto-scrolls the transcript on every messages change', async () => {
    const scrollIntoViewSpy = vi.spyOn(Element.prototype, 'scrollIntoView');
    renderAtChatRoute('stub-9');

    // One call on mount (the initial, empty `messages` still triggers the
    // effect once), then at least one more once the user's own message is
    // appended to the transcript.
    const callsBeforeSend = scrollIntoViewSpy.mock.calls.length;
    await userEvent.type(screen.getByLabelText('chat-input'), 'hello');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(scrollIntoViewSpy.mock.calls.length).toBeGreaterThan(callsBeforeSend),
    );
    expect(scrollIntoViewSpy).toHaveBeenCalledWith({ behavior: 'smooth' });

    await waitFor(() => expect(screen.getByText(/stub taxpayer reply\./)).toBeInTheDocument());
    await waitFor(() => expect(useTaxpayerChatStore.getState().messages).toHaveLength(1));
    scrollIntoViewSpy.mockRestore();
  });
});
