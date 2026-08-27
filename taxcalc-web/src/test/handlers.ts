// src/test/handlers.ts
//
// MSW request handlers standing in for the W3 D2 REST backend, the W3 D4
// GraphQL backend, and (via sseHandlers, spread in below) the W4 D4 /api/chat
// streaming proxy. `graphql.query`/`graphql.mutation` match by operation
// name regardless of endpoint URL, so these work against whatever host
// `apollo/client.ts` is configured with. `http.get` matches the literal
// REST URL `useGetTaxLiabilityRest` calls.
import { delay, graphql, http, HttpResponse } from 'msw';
import { sseHandlers } from './sse-handlers';

const LATEST_TAXPAYERS_DATA = {
  latestTaxpayers: [
    {
      __typename: 'Taxpayer',
      id: 'stub-1',
      tags: ['flagged'],
      lines: [{ __typename: 'LineItem', id: 'line-1', description: 'Wages', amount: 100 }],
    },
    { __typename: 'Taxpayer', id: 'stub-2', tags: [], lines: [] },
    { __typename: 'Taxpayer', id: 'stub-3', tags: [], lines: [] },
  ],
};

function taxpayerRestBody(id: string): Record<string, unknown> {
  return {
    id,
    displayName: 'stub taxpayer',
    filingStatus: 'SINGLE',
    homeJurisdiction: 'COLORADO',
    createdAt: '2025-01-04T00:00:00Z',
    liabilities: [
      {
        taxYear: 2024,
        bracketId: 'ca-bracket-1',
        taxableAmount: 10000,
        liabilityAmount: 100,
        computedAt: '2025-01-04T00:00:00Z',
      },
    ],
    tags: [],
  };
}

export const handlers = [
  ...sseHandlers,

  graphql.query('LatestTaxpayers', () => HttpResponse.json({ data: LATEST_TAXPAYERS_DATA })),

  graphql.mutation('SummarizeTaxpayer', async () => {
    // A short, deliberate delay so TaxpayerSummaryPage.test.tsx has a real
    // window to observe the optimisticResponse placeholder before this
    // handler's response replaces it - without it, an in-memory resolver
    // can settle within the same microtask flush the placeholder renders
    // in, and the test never actually observes two distinct render states.
    await delay(200);
    return HttpResponse.json({
      data: {
        summarizeTaxpayer: {
          __typename: 'TaxpayerSummary',
          filingStatus: 'SINGLE',
          totalLiability: 8420,
          jurisdictionCount: 2,
          riskBand: 'LOW',
        },
      },
    });
  }),

  http.get('http://localhost:8080/api/v1/taxpayers/:id', ({ params }) =>
    HttpResponse.json(taxpayerRestBody(String(params.id))),
  ),
];

/**
 * Opt-in 500 for `GET /api/v1/taxpayers/:id`. `server.use(taxpayerRestErrorHandler)`
 * overrides only this one route for the rest of the current test - the
 * GraphQL handlers above stay on their happy path - so integration tests
 * that need both a working list query and a failing REST fetch in the same
 * render don't have to rewrite the whole handler array.
 */
export const taxpayerRestErrorHandler = http.get(
  'http://localhost:8080/api/v1/taxpayers/:id',
  () => HttpResponse.json({ error: 'liability service unavailable' }, { status: 500 }),
);

/**
 * Opt-in artificially-delayed variant of the `LatestTaxpayers` happy path.
 * The default handler above resolves near-instantly, so a test asserting
 * "the role=status skeleton renders, then the data replaces it" only works
 * today because a promise always settles at least one microtask after the
 * synchronous render - true, but not a deliberate, robust signal. This
 * handler gives that assertion a real, generous window instead of relying
 * on that timing coincidence.
 */
export const latestTaxpayersLoadingHandler = graphql.query('LatestTaxpayers', async () => {
  await delay(50);
  return HttpResponse.json({ data: LATEST_TAXPAYERS_DATA });
});

/** Opt-in artificially-delayed variant of the REST happy path - see {@link latestTaxpayersLoadingHandler}'s doc comment for why this exists. */
export const taxpayerRestLoadingHandler = http.get(
  'http://localhost:8080/api/v1/taxpayers/:id',
  async ({ params }) => {
    await delay(50);
    return HttpResponse.json(taxpayerRestBody(String(params.id)));
  },
);
