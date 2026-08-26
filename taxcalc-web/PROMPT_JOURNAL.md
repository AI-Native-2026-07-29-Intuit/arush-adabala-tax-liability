# Prompt Journal — W4 D4

Five prompts from this deliverable's AI-assisted session that produced shipped code, each with what the model produced and the verdict on whether it was accepted as-is, accepted with changes, or reworked.

---

## 1. `server/api/chat.ts` — the streaming proxy route

**Prompt:** "Create the server entry at `server/index.ts` and the route at `server/api/chat.ts`. The route should read `messages` from the JSON body, call `streamText` against the W3 D4 Spring AI endpoint via `createOpenAICompatible`, and return `toDataStreamResponse` with the SSE headers `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`. Forward the incoming request's abort signal into `streamText`."

**Model output:** `server/index.ts` (a Hono app, mounting the chat route) and `server/api/chat.ts` (the route itself), matching the spec.

**Verdict:** Accepted, with one addition beyond what was literally asked: the model flagged that Hono itself is runtime-agnostic and has no way to actually listen on a port under Node without an adapter, and added `@hono/node-server` as a dependency — not in the lesson's package list (`ai @ai-sdk/react @ai-sdk/openai-compatible zod hono`). Checked this independently: correct, `serve()` comes from that package, not `hono` itself. Kept.

---

## 2. Fixing `useTaxpayerChatStore`'s silent persistence failure

**Prompt:** "The chat store's persisted messages aren't showing up when I read them back — can you check why and fix it?"

**Model output:** Ran a direct probe and found `window.localStorage` is genuinely `undefined` under this project's exact Node/jsdom/Vitest combination (not merely untested — actually broken). Extracted `useTaxpayerFilterStore`'s existing local `safeLocalStorage` fallback (which had already worked around the identical issue) into a shared `src/lib/safeLocalStorage.ts`, and pointed both stores at it via `createJSONStorage`.

**Verdict:** Accepted. The alternative — writing a second, near-duplicate fallback scoped only to the new store — would have left two copies of the same workaround to keep in sync. Sharing it was the right call.

---

## 3. Making Stop's cancellation genuinely testable

**Prompt:** "The Stop-mid-stream test can't actually verify cancellation happened — it's just documented as a test-environment limitation. Can you find the real root cause and fix it properly instead of leaving it as a known gap?"

**Model output:** Traced the failure precisely (not guessed): undici's webidl `AbortSignal` converter does a strict `instanceof` check against its own module-scoped class reference, and vitest's jsdom environment setup hardcodes `AbortController`/`AbortSignal` into the fixed list of globals it copies from `window`, unconditionally shadowing Node's native ones for every test file — confirmed by reading both `undici/lib/web/webidl/index.js` and vitest's own environment-population source. Rewrote `test/server.ts`'s existing fetch wrapper to reimplement cancellation at the response body-stream level instead of just stripping the incompatible signal.

**Verdict:** Accepted, but only after independent verification — wrote a throwaway probe test first to confirm the message genuinely froze mid-stream and `isLoading` flipped to `false` for real, not on a lucky timing race, before rewriting the actual test suite around it. This also retroactively fixes the identical, previously-unaddressed gap in Apollo's own cancellation from W4 D3.

---

## 4. The literal 5xx-mapping middleware

**Prompt:** "I did not write a separate Hono middleware function [for the 5xx mapping] — was that actually a requirement? [...] Can you fix this issue."

**Model output:** Added `mapUpstreamErrors`, a custom `fetch` passed to `createOpenAICompatible({ fetch })` — an option the AI SDK's own type doc names as exactly this ("a custom fetch implementation you can use as a middleware to intercept requests") — plus an `UpstreamStatusError` class and an updated `toClientErrorMessage` that prefers its message when present.

**Verdict:** Accepted after verifying against a hand-rolled Node `http` stub returning a genuine `500` with a JSON body (not just connection-refused, which was the only case tested before). Confirmed it's a real improvement, not just a wording fix: the mapped error short-circuits in exactly one outbound request, versus the prior `getErrorMessage`-only approach's three retries before surfacing the same message.

---

## 5. A stub backend to demonstrate the happy path live

**Prompt:** "For this part [showing real tokens streaming through a live browser], could you stand up a minimal stub backend?"

**Model output:** `dev/stub-spring-ai.ts` (`pnpm stub-backend`, port 8080) — a Hono server speaking the genuine OpenAI-compatible chat-completions streaming wire format (not the Vercel data-stream protocol the browser sees; one level further upstream, so `streamText`'s real parsing path runs, not a bypass), with canned `GET /api/v1/taxpayers/:id` and `?year=` responses for the two tools.

**Verdict:** Accepted, but only after live verification through an actual Chromium session (not just `pnpm typecheck`/`pnpm test` passing) — confirmed real streamed tokens rendering word-by-word, and a genuine two-step tool-calling exchange through the production code path. That same verification pass surfaced an unrelated real issue — `useTaxpayerChatStore`'s flat, non-taxpayer-scoped array means a second taxpayer's chat panel shows the first taxpayer's completed turns too — which was flagged separately rather than silently fixed or ignored, since changing the store's shape wasn't part of what was asked here.
