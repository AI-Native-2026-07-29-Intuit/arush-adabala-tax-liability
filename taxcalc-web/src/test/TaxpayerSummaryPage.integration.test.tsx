// src/test/TaxpayerSummaryPage.integration.test.tsx
//
// TaxpayerSummaryPage.test.tsx (Task 1) drives this page through Apollo's
// MockedProvider, which matches each mock by exact query + variables.
// This file instead drives it through a real ApolloClient over HttpLink,
// intercepted by MSW's operation-name-based matching (graphql.mutation
// matches any request named 'SummarizeTaxpayer', regardless of the exact
// variables sent) - a genuinely different validation surface than
// MockedProvider's strict per-call matching, and the one this app's real
// production ApolloClient (src/apollo/client.ts) actually resembles.
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { ApolloClient, HttpLink, InMemoryCache, type NormalizedCacheObject } from '@apollo/client';
import { graphql, HttpResponse } from 'msw';
import { server } from './server';
import { renderWithProviders } from './renderWithProviders';
import { TaxpayerSummaryPage } from '../pages/TaxpayerSummaryPage';

function newSharedApolloClient(): ApolloClient<NormalizedCacheObject> {
  return new ApolloClient({
    link: new HttpLink({ uri: 'http://localhost:8080/graphql' }),
    cache: new InMemoryCache(),
  });
}

function renderAtSummaryRoute(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/taxpayers/:id/summary" element={<TaxpayerSummaryPage></TaxpayerSummaryPage>} />
    </Routes>,
    { apolloClient: newSharedApolloClient(), initialEntries: ['/taxpayers/stub-1/summary'] },
  );
}

describe('TaxpayerSummaryPage integration (real ApolloClient + MSW)', () => {
  it('summarizes a taxpayer through the real MSW-backed GraphQL mutation', async () => {
    const { user } = renderAtSummaryRoute();

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    // handlers.ts's default SummarizeTaxpayer handler carries a genuine
    // network delay, so the optimistic placeholder is observable first.
    expect(screen.getAllByText('PENDING')).toHaveLength(2);
    expect(await screen.findByText('SINGLE')).toBeInTheDocument();
    expect(screen.getByText('8420')).toBeInTheDocument();
  });

  it('surfaces a role="alert" error banner when the real network mutation fails', async () => {
    server.use(
      graphql.mutation('SummarizeTaxpayer', () =>
        HttpResponse.json({ errors: [{ message: 'summary service down' }] }),
      ),
    );
    const { user } = renderAtSummaryRoute();

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/summary service down/i);
  });

  it('re-summarizing after a failure hits the network again and can succeed', async () => {
    server.use(
      graphql.mutation('SummarizeTaxpayer', () =>
        HttpResponse.json({ errors: [{ message: 'summary service down' }] }),
      ),
    );
    const { user } = renderAtSummaryRoute();
    await user.click(screen.getByRole('button', { name: 'Summarize' }));
    await screen.findByRole('alert');

    // server.use overrides one handler, not all of them - resetHandlers
    // (bound in setupTests.ts's afterEach) would also restore the default,
    // but here the point is a second click while the override is already
    // gone, exercising the same route this file's happy-path test does.
    server.resetHandlers();
    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    expect(await screen.findByText('SINGLE')).toBeInTheDocument();
  });
});
