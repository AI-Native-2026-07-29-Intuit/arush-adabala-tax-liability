// src/test/TaxpayerListPage.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ApolloClient, ApolloProvider, HttpLink, InMemoryCache } from '@apollo/client';
import { MemoryRouter } from 'react-router-dom';
import { TaxpayerListPage } from '../pages/TaxpayerListPage';

/** A fresh, uncached client per render - MSW backs the network call, not Apollo's own mocks. */
function newTestClient(): ApolloClient<unknown> {
  return new ApolloClient({
    link: new HttpLink({ uri: 'http://localhost:8080/graphql' }),
    cache: new InMemoryCache({ typePolicies: { Taxpayer: { keyFields: ['id'] } } }),
  });
}

describe('TaxpayerListPage', () => {
  it('renders three rows once the MSW handler resolves', async () => {
    render(
      <ApolloProvider client={newTestClient()}>
        <MemoryRouter>
          <TaxpayerListPage></TaxpayerListPage>
        </MemoryRouter>
      </ApolloProvider>,
    );

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3));
    expect(screen.getByText('stub-1')).toBeInTheDocument();
  });
});
