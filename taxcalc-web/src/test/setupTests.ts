// src/test/setupTests.ts
import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll, expect } from 'vitest';
import { toHaveNoViolations } from 'jest-axe';
import { server } from './server';

// jest-axe's `toHaveNoViolations` export is already the matcher-map shape
// expect.extend wants ({ toHaveNoViolations: fn }), not a bare function -
// `expect.extend({ toHaveNoViolations })` would wrap it one level too deep
// and register a matcher whose "function" is actually an object, which
// vitest's expect then fails to call at all.
expect.extend(toHaveNoViolations);

declare module 'vitest' {
  interface Assertion<T> {
    toHaveNoViolations(): T;
  }
}

/**
 * A plain `Error` (not `DOMException` - jsdom's `DOMException` is a
 * separate class from Node's native one for the same realm-mismatch
 * reason as `AbortController`, and sidestepping that entirely is simpler
 * than checking) whose `name` matches what `isAbortError` checks for.
 */
function newAbortError(): Error {
  const error = new Error('The operation was aborted.');
  error.name = 'AbortError';
  return error;
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });

  // jsdom ships its own AbortController/AbortSignal (the DOM spec requires
  // them for EventTarget), a genuinely distinct class from the one Node's
  // native fetch/Request (built on undici) validates `init.signal` against
  // internally via a webidl `instanceof` check
  // (undici/lib/web/webidl/index.js's `MakeTypeAssertion`, checked against
  // undici's own module-scoped `AbortSignal` reference - not whatever
  // `globalThis.AbortSignal` currently is). Passing a jsdom-constructed
  // signal straight through trips that check with "Expected signal to be
  // an instance of AbortSignal" before a handler even runs. There's no
  // supported way to unify the two classes from test code: vitest's own
  // jsdom environment setup hardcodes AbortController/AbortSignal into the
  // fixed list of globals it copies from `window` onto `globalThis` for
  // every test file, unconditionally overwriting Node's native ones with
  // jsdom's - confirmed by reading vitest's own environment-population
  // source, not assumed.
  //
  // Stripping the signal before the real fetch call avoids that crash, but
  // on its own would silently disable cancellation for every test in the
  // suite. Instead, this reimplements cancellation at the body-stream
  // level: once the caller's (jsdom-realm) signal aborts, the wrapped
  // stream below errors with a plain `Error` whose `.name` is
  // `'AbortError'` - the only thing `@ai-sdk/provider-utils`'s
  // `isAbortError` actually checks (`error instanceof Error &&
  // error.name === 'AbortError'`, no class-identity check at all) - so
  // useChat's stop() and Apollo's AbortController-based cancellation both
  // genuinely interrupt an in-flight request under test, the same as they
  // would in a real browser with one unified AbortController class.
  const interceptedFetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const signal = init?.signal;
    if (!signal) return interceptedFetch(input, init);

    const rest = { ...init };
    delete rest.signal;

    if (signal.aborted) {
      throw newAbortError();
    }

    const response = await interceptedFetch(input, rest);
    if (!response.body) return response;

    const reader = response.body.getReader();
    const cancelableBody = new ReadableStream<Uint8Array>({
      async pull(controller) {
        const abortedWhileWaiting = new Promise<never>((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(newAbortError()), { once: true });
        });
        try {
          const { done, value } = await Promise.race([reader.read(), abortedWhileWaiting]);
          if (done) {
            controller.close();
            return;
          }
          controller.enqueue(value);
        } catch (error) {
          controller.error(error);
          await reader.cancel().catch(() => undefined);
        }
      },
      cancel: (reason) => reader.cancel(reason),
    });

    return new Response(cancelableBody, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  });
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// jsdom doesn't implement Element.scrollIntoView (layout is entirely
// unmeasured under jsdom, so there's nothing meaningful to scroll to) -
// TaxpayerChatPanel's auto-scroll-on-new-message effect calls it
// unconditionally, so every test rendering that component needs a stub or
// the effect throws.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// `window.localStorage` is genuinely `undefined` under this Node/jsdom/
// Vitest combination by default (see safeLocalStorage.ts's own doc
// comment) - real only in the handful of tests that `vi.stubGlobal` it -
// so this is guarded with optional chaining rather than a bare `.clear()`,
// which would throw in every other test file. Where it IS stubbed, this
// keeps the W4 D4 Zustand chat store (and any other localStorage-backed
// store) from leaking a persisted write across tests in the same file.
afterEach(() => {
  window.localStorage?.clear();
});
