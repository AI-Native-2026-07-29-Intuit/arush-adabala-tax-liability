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
public/mocks/taxpayer.json   stubbed read-model JSON (see below)
src/types/taxpayer.ts        Taxpayer / TaxpayerLine types
src/hooks/useTaxpayer.ts     fetch + discriminated-union loading state
src/components/              ThresholdSlider, ThresholdReadout
src/pages/TaxpayerDetailPage.tsx
src/App.tsx                  hash routing (temporary, see Status)
src/test/                    Vitest setup + smoke tests
```

## Status

This is Day 1 of the frontend track — a working scaffold, not yet wired to
the live backend:

- **Data is stubbed.** `useTaxpayer` fetches `public/mocks/taxpayer.json`
  directly; it does not call the backend's `/api/v1/taxpayers/{id}` or
  `/graphql` endpoints yet. Money fields (`totalLiability`, each line's
  `amount`) are kept as strings, mirroring the backend's `BigDecimal`
  convention — JS `number` is IEEE-754 binary64 and loses cents at scale.
- **Routing is a placeholder.** `App.tsx` renders `TaxpayerDetailPage` only
  when `window.location.hash` is `#/taxpayers/stub-id-1`, otherwise a tiny
  "go to ..." message. TanStack Router replaces this.
- **State is local `useState`.** Zustand lands next.

Planned, in order: Zustand for shared state (W4 D2) → Apollo Client +
TanStack Router, replacing the mock JSON and hash routing with a real
`/graphql` query (W4 D3) → streaming AI responses (W4 D4) → the full
frontend testing/production-readiness pass (W4 D5).
