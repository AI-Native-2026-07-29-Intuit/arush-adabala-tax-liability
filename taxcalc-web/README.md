# taxcalc-web

Frontend for the Real-Time Tax Liability Calculator capstone — a Vite +
React 19 + strict TypeScript project, peer to the Spring Boot backend at the
repo root. See the root [`README.md`](../README.md#week-4-day-1--modern-react-with-vite--strict-typescript)
for how this fits into the overall capstone.

## Quickstart

```bash
pnpm install
pnpm dev         # http://localhost:5173/#/taxpayers/stub-id-1
```

## Scripts

| Command | Does |
|---|---|
| `pnpm dev` | Start the Vite dev server |
| `pnpm build` | Typecheck, then produce a production bundle in `dist/` |
| `pnpm preview` | Serve the built `dist/` bundle locally |
| `pnpm lint` | ESLint 9 (flat config, `eslint.config.js`) |
| `pnpm typecheck` | `tsc --noEmit` against the strict `tsconfig.json` |
| `pnpm test` | Vitest, jsdom environment |

`pnpm lint && pnpm typecheck && pnpm test && pnpm build` is the same gate
`.github/workflows/web-ci.yml` runs on every PR touching `taxcalc-web/**`.

## Layout

```
public/mocks/taxpayer.json          stubbed read-model JSON (see below)
src/types/taxpayer.ts               Taxpayer / TaxpayerLine types
src/hooks/useTaxpayer.ts            W4 D1 fetch hook (unused by the page since W4 D2; kept unchanged)
src/hooks/useDebouncedSearch.ts     debounces the store's searchText slice
src/stores/useTaxpayerFilterStore.ts  Zustand store: filters + threshold, devtools + persist
src/components/                     FilterStrip, ThresholdSlider, ThresholdReadout, ErrorBoundary
src/pages/TaxpayerDetailPage.tsx        detail page, driven by a useReducer state machine
src/pages/TaxpayerDetailPage.reducer.ts pure reducer + discriminated-union DetailState
src/App.tsx                         hash routing (temporary, see Status) + ErrorBoundary wiring
src/test/                           Vitest setup + unit/smoke tests
```

## Status

Day 2 of the frontend track — state management landed, still not wired to
the live backend:

- **Data is stubbed.** `TaxpayerDetailPage` fetches `public/mocks/taxpayer.json`
  directly inside its own effect; it does not call the backend's
  `/api/v1/taxpayers/{id}` or `/graphql` endpoints yet. Money fields
  (`totalLiability`, each line's `amount`) are kept as strings, mirroring
  the backend's `BigDecimal` convention — JS `number` is IEEE-754 binary64
  and loses cents at scale.
- **Routing is a placeholder.** `App.tsx` renders `TaxpayerDetailPage` only
  when `window.location.hash` is `#/taxpayers/stub-id-1`, otherwise a tiny
  "go to ..." message. TanStack Router replaces this on W4 D3.
- **State is Zustand + useReducer.** `useTaxpayerFilterStore` (W4 D2) holds
  the filter strip's fields and the threshold slider; `TaxpayerDetailPage`'s
  fetch lifecycle is a `useReducer` state machine instead of ad-hoc
  `useState`. See [Week 4 Day 2](../README.md#week-4-day-2--react-hooks-zustand--error-boundaries)
  in the root README for the full breakdown.

Planned, in order: Apollo Client + TanStack Router, replacing the mock JSON
and hash routing with a real `/graphql` query (W4 D3) → streaming AI
responses (W4 D4) → the full frontend testing/production-readiness pass
(W4 D5).
