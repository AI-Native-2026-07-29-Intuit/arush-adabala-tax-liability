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
```

Sign in with the stub button, which writes a fake JWT to `localStorage`
and lands on `/taxpayers`. The REST and GraphQL calls this app makes
target `http://localhost:8080` — the backend at the repo root — so real
data requires that server (and, for `/api/**`, a genuine JWT from its
configured IdP) running locally; without it, both routes' loading states
resolve to network errors, which is expected outside a full backend setup.

## Scripts

| Command | Does |
|---|---|
| `pnpm dev` | Start the Vite dev server |
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
src/router.tsx                      createBrowserRouter + ProtectedLayout route guard
src/hooks/useGetTaxLiabilityRest.ts TanStack Query hook against GET /api/v1/taxpayers/{id}
src/hooks/useDebouncedSearch.ts     debounces the store's searchText slice
src/stores/useTaxpayerFilterStore.ts  Zustand store: filters + threshold, devtools + persist
src/components/                     FilterStrip, ThresholdSlider, ThresholdReadout, ErrorBoundary
src/pages/LoginPage.tsx             stub sign-in, writes a fake JWT and navigates to /taxpayers
src/pages/TaxpayerListPage.tsx      Apollo useQuery over latestTaxpayers
src/pages/TaxpayerSummaryPage.tsx   Apollo useMutation over summarizeTaxpayer, optimistic placeholder
src/pages/TaxpayerDetailPage.tsx        detail page, driven by a useReducer state machine
src/pages/TaxpayerDetailPage.reducer.ts pure reducer + discriminated-union DetailState
src/main.tsx                        entry point: Apollo/Query providers wrap ErrorBoundary + RouterProvider
src/test/handlers.ts, server.ts     MSW request handlers + setupServer lifecycle
src/test/                           Vitest setup + unit/smoke tests
```

## Status

Day 3 of the frontend track — wired to both live backends, no more mock
JSON or hash routing:

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
- **State is still Zustand + useReducer.** Unchanged from W4 D2 — see
  [Week 4 Day 2](../README.md#week-4-day-2--react-hooks-zustand--error-boundaries)
  in the root README.

Planned: streaming AI responses (W4 D4) → the full frontend testing/
production-readiness pass (W4 D5).
