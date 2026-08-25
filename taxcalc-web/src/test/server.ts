// src/test/server.ts
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { handlers } from './handlers';

export const server = setupServer(...handlers);

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });

  // jsdom ships its own AbortController/AbortSignal (the DOM spec requires
  // them for EventTarget), separate from Node's native undici classes.
  // Apollo's HttpLink builds an AbortController per request for
  // cancellation support; under jsdom that construction uses jsdom's
  // class, and MSW's fetch interceptor - installed by server.listen()
  // above, and which itself reconstructs a native `Request` to inspect
  // every intercepted call - rejects that signal with "Expected signal to
  // be an instance of AbortSignal" before a handler even runs. No test
  // here exercises request cancellation, so wrapping the interceptor's own
  // patched `fetch` to drop an incompatible signal (rather than trying to
  // fake cancellation across realms) is the simplest fix.
  const interceptedFetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (!init?.signal) return interceptedFetch(input, init);
    const rest = { ...init };
    delete rest.signal;
    return interceptedFetch(input, rest);
  }) as typeof globalThis.fetch;
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
