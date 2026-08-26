# taxcalc-web

Frontend for the Real-Time Tax Liability Calculator capstone — a Vite +
React 19 + strict TypeScript project, peer to the Spring Boot backend at the
repo root. See the root [`README.md`](../README.md#week-4-day-1--modern-react-with-vite--strict-typescript)
for how this fits into the overall capstone.

## Quickstart

```bash
pnpm install
pnpm codegen     # regenerate src/gql/generated/ from schema.graphqls
pnpm dev         # http://localhost:5173/login
pnpm server      # separate terminal: the /api/chat streaming proxy on :3001
```

Sign in with the stub button, which writes a fake JWT to `localStorage`
and lands on `/taxpayers`. The REST and GraphQL calls this app makes
target `http://localhost:8080` — the backend at the repo root — so real
data requires that server (and, for `/api/**`, a genuine JWT from its
configured IdP) running locally; without it, both routes' loading states
resolve to network errors, which is expected outside a full backend setup.
`/taxpayers/:id/chat`'s streaming assistant needs both `pnpm server`
running *and* the backend's `POST /ai/chat` endpoint reachable at
`localhost:8080/ai` — see [Week 4 Day 4](../README.md#week-4-day-4--vercel-ai-sdk-streaming-responses-streamed-tool-calls--msw-sse-tests)
in the root README for what's actually wired up versus assumed. Since
that endpoint (and a docker-compose to run it) doesn't exist anywhere in
this repo, `pnpm stub-backend` starts a minimal stand-in on `:8080` -
purely so the chat happy path is demonstrable in a real browser without
the full Spring stack, not a substitute for building the real endpoint:

```bash
pnpm dev            # :5173
pnpm server          # :3001 - separate terminal
pnpm stub-backend    # :8080 - separate terminal
```

## Scripts

| Command | Does |
|---|---|
| `pnpm dev` | Start the Vite dev server |
| `pnpm server` | Start the Hono `/api/chat` streaming proxy (`server/index.ts`, `:3001`) |
| `pnpm stub-backend` | Start the dev-only stand-in for the (nonexistent) W3 D4 Spring AI + REST backend (`dev/stub-spring-ai.ts`, `:8080`) |
| `pnpm build` | Typecheck, then produce a production bundle in `dist/` |
| `pnpm preview` | Serve the built `dist/` bundle locally |
| `pnpm lint` | ESLint 9 (flat config, `eslint.config.js`) |
| `pnpm typecheck` | `tsc --noEmit` against the strict `tsconfig.json` |
| `pnpm test` | Vitest, jsdom environment |
| `pnpm codegen` | GraphQL Codegen, schema → `src/gql/generated/` |
| `pnpm codegen:watch` | Same, re-running on file changes |

`pnpm lint && pnpm typecheck && pnpm test && pnpm build` is the same gate
`.github/workflows/web-ci.yml` runs on every PR touching `taxcalc-web/**`.

## Layout

```
codegen.ts                          GraphQL Codegen config (schema: the backend's checked-in .graphqls file)
src/queries/*.graphql               GraphQL documents codegen turns into typed documents
src/gql/generated/                  codegen output (generated - do not hand-edit; hooks.ts has useLatestTaxpayersQuery/useSummarizeTaxpayerMutation)
src/apollo/client.ts                ApolloClient: typed InMemoryCache + JWT setContext auth link
src/queryClient.ts                  TanStack QueryClient for REST traffic
src/lib/jwtStorage.ts               shared getStoredJwt/setStoredJwt (localStorage, try/catch)
src/lib/safeLocalStorage.ts         Zustand StateStorage that falls back to an in-memory Map when window.localStorage is missing/throws
src/router.tsx                      createBrowserRouter + ProtectedLayout route guard
src/hooks/useGetTaxLiabilityRest.ts TanStack Query hook against GET /api/v1/taxpayers/{id}
src/hooks/useDebouncedSearch.ts     debounces the store's searchText slice
src/stores/useTaxpayerFilterStore.ts  Zustand store: filters + threshold, devtools + persist
src/stores/useTaxpayerChatStore.ts    Zustand store: persisted chat history, written only from useChat's onFinish
src/components/                     FilterStrip, ThresholdSlider, ThresholdReadout, ErrorBoundary
src/pages/LoginPage.tsx             stub sign-in, writes a fake JWT and navigates to /taxpayers
src/pages/TaxpayerListPage.tsx      Apollo useQuery over latestTaxpayers
src/pages/TaxpayerSummaryPage.tsx   Apollo useMutation over summarizeTaxpayer, optimistic placeholder
src/pages/TaxpayerDetailPage.tsx        detail page, driven by a useReducer state machine
src/pages/TaxpayerDetailPage.reducer.ts pure reducer + discriminated-union DetailState
src/pages/TaxpayerChatPanel.tsx     streaming chat assistant (useChat), mounted at /taxpayers/:id/chat
src/pages/ToolCallCard.tsx          presentational card for one ToolInvocation (partial-call/call/result)
src/main.tsx                        entry point: Apollo/Query providers wrap ErrorBoundary + RouterProvider
server/index.ts                     Hono entry point (pnpm server), mounts the chat route on :3001
server/api/chat.ts                  POST /api/chat: validates the request body (zod), then streamText + toDataStreamResponse against the Spring AI backend
server/api/chat-tools.ts            lookupTaxpayer/estimateLiability ai tools; each validates its REST response (zod) before returning it as the tool result
dev/stub-spring-ai.ts               dev-only stand-in for the (nonexistent) Spring AI + REST backend (pnpm stub-backend, :8080) - not a real implementation, just enough to demo the chat happy path
src/test/handlers.ts, server.ts     MSW request handlers + setupServer lifecycle
src/test/sse-handlers.ts            MSW handler emitting the AI SDK data-stream protocol by hand (no live proxy needed for tests)
src/test/chat.test.ts               chat.ts's request-body validation, exercised directly via Hono's own .request() helper
src/test/chat-tools.test.ts         both tools' REST-response validation, happy and malformed paths, via MSW
src/test/                           Vitest setup + unit/smoke tests
```

## Status

Day 4 of the frontend track — a streaming chat assistant sits alongside the
W4 D3 data layer:

- **Data is live.** `TaxpayerListPage`/`TaxpayerSummaryPage` query and
  mutate the backend's `/graphql` endpoint via Apollo Client;
  `TaxpayerDetailPage` fetches `GET /api/v1/taxpayers/{id}` via a TanStack
  Query hook. `public/mocks/taxpayer.json`, `hooks/useTaxpayer.ts`, and
  `types/taxpayer.ts` (all W4 D1 stubs) are deleted.
- **Routing is real.** `router.tsx`'s `createBrowserRouter` replaces the
  W4 D1/D2 hash-routing placeholder; every `/taxpayers*` route sits behind
  a `ProtectedLayout` that redirects to `/login` when no JWT is present in
  `localStorage`.
- **Auth is a client-side stub.** `LoginPage` writes a fake token — good
  enough to exercise the route guard, but the backend's own OAuth2
  resource server (a real IdP) is what actually authorizes `/api/**`
  calls; `/graphql` itself is unauthenticated in the current backend
  config. See the root README's Week 4 Day 3 section for the full
  breakdown, including the `useMutation`-doesn't-see-`optimisticResponse`
  gotcha and the jsdom/MSW `AbortSignal` fix.
- **State is Zustand + useReducer**, now two stores: W4 D2's filter store
  and W4 D4's `useTaxpayerChatStore`. Both go through the new
  `lib/safeLocalStorage.ts` (extracted from the filter store's original
  workaround) — `window.localStorage` is genuinely `undefined` under this
  Node/jsdom/Vitest combination, confirmed by direct probe while wiring up
  the chat store's persistence.
- **Streaming chat is new.** `TaxpayerChatPanel` (`/taxpayers/:id/chat`)
  runs the Vercel AI SDK's `useChat` against a thin Hono proxy
  (`server/api/chat.ts`, `pnpm server` on `:3001`) that streams the W3 D4
  Spring AI backend's reply via `streamText` + `toDataStreamResponse`. That
  backend (and a docker-compose to run it) doesn't exist anywhere in this
  repo, so `pnpm stub-backend` (`dev/stub-spring-ai.ts`, `:8080`) stands in
  with real OpenAI-compatible streaming completions and canned REST
  responses — enough to drive the whole happy path, tool calls included,
  in an actual browser. See the root README's Week 4 Day 4 section for the
  full breakdown, including why `ai`/`@ai-sdk/react` are pinned to the v4
  line, the two tool calls, the persist-only-on-`onFinish` rule, and how
  Stop's cancellation was made genuinely testable by fixing the underlying
  jsdom `AbortSignal` gap rather than working around it.

Planned: the full frontend testing/production-readiness pass (W4 D5).
