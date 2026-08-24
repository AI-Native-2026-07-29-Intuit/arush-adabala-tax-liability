// src/test/TaxpayerSummaryPage.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ApolloClient, ApolloProvider, HttpLink, InMemoryCache } from '@apollo/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { TaxpayerSummaryPage } from '../pages/TaxpayerSummaryPage';

function newTestClient(): ApolloClient<unknown> {
  return new ApolloClient({
    link: new HttpLink({ uri: 'http://localhost:8080/graphql' }),
    cache: new InMemoryCache(),
  });
}

function renderAtSummaryRoute(): void {
  render(
    <ApolloProvider client={newTestClient()}>
      <MemoryRouter initialEntries={['/taxpayers/stub-1/summary']}>
        <Routes>
          <Route path="/taxpayers/:id/summary" element={<TaxpayerSummaryPage></TaxpayerSummaryPage>} />
        </Routes>
      </MemoryRouter>
    </ApolloProvider>,
  );
}

describe('TaxpayerSummaryPage', () => {
  it('shows the optimistic placeholder immediately, then the server result', async () => {
    renderAtSummaryRoute();

    fireEvent.click(screen.getByRole('button', { name: 'Summarize' }));

    // The MSW handler delays its response (see handlers.ts) precisely so
    // this optimistic placeholder has a real window to be observed before
    // the server result replaces it. filingStatus and riskBand both carry
    // the same placeholder text.
    await waitFor(() => expect(screen.getAllByText('PENDING')).toHaveLength(2));

    await waitFor(() => expect(screen.getByText('SINGLE')).toBeInTheDocument());
    expect(screen.getByText('LOW')).toBeInTheDocument();
  });
});
