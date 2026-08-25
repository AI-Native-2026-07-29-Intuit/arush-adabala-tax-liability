// src/test/handlers.ts
//
// MSW request handlers standing in for the W3 D2 REST backend and the W3
// D4 GraphQL backend. `graphql.query`/`graphql.mutation` match by
// operation name regardless of endpoint URL, so these work against
// whatever host `apollo/client.ts` is configured with. `http.get` matches
// the literal REST URL `useGetTaxLiabilityRest` calls.
import { delay, graphql, http, HttpResponse } from 'msw';

export const handlers = [
  graphql.query('LatestTaxpayers', () =>
    HttpResponse.json({
      data: {
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
      },
    }),
  ),

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
    HttpResponse.json({
      id: String(params.id),
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
    }),
  ),
];
