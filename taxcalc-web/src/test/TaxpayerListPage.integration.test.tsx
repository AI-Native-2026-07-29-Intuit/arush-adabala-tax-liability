// src/test/TaxpayerListPage.integration.test.tsx
//
// Multi-page integration: the list route, the router, and the detail
// route's own MSW-backed REST fetch all run together, plus an
// Apollo-cache-hit scenario that a single-component test can't exercise
// (it needs the same ApolloClient instance reused across two mounts).
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { ApolloClient, HttpLink, InMemoryCache, type NormalizedCacheObject } from '@apollo/client';
import { renderWithProviders } from './renderWithProviders';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';
import { safeLocalStorage } from '../lib/safeLocalStorage';
import { TaxpayerListPage } from '../pages/TaxpayerListPage';
import { TaxpayerDetailPage } from '../pages/TaxpayerDetailPage';

function newSharedApolloClient(): ApolloClient<NormalizedCacheObject> {
  return new ApolloClient({
    link: new HttpLink({ uri: 'http://localhost:8080/graphql' }),
    cache: new InMemoryCache(),
  });
}

function renderApp(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/taxpayers" element={<TaxpayerListPage></TaxpayerListPage>} />
      <Route path="/taxpayers/:id" element={<TaxpayerDetailPage></TaxpayerDetailPage>} />
    </Routes>,
    { initialEntries: ['/taxpayers'] },
  );
}

beforeEach(() => {
  useTaxpayerFilterStore.getState().reset();
});

describe('TaxpayerListPage integration (Apollo cache + router + REST)', () => {
  it('shows the list before showing the loading skeleton on a fresh Apollo client', async () => {
    const client = newSharedApolloClient();
    renderWithProviders(<TaxpayerListPage></TaxpayerListPage>, { apolloClient: client, initialEntries: ['/taxpayers'] });

    expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
    await waitFor(() => expect(screen.getByText('stub-1')).toBeInTheDocument());
  });

  it('reads from the Apollo cache on a second mount with the same client - no loading flash', async () => {
    const client = newSharedApolloClient();
    const first = renderWithProviders(<TaxpayerListPage></TaxpayerListPage>, {
      apolloClient: client,
      initialEntries: ['/taxpayers'],
    });
    await waitFor(() => expect(screen.getByText('stub-1')).toBeInTheDocument());
    first.unmount();

    renderWithProviders(<TaxpayerListPage></TaxpayerListPage>, { apolloClient: client, initialEntries: ['/taxpayers'] });

    // cache-first (Apollo's default fetchPolicy) resolves synchronously
    // from the client's normalized cache, so the loading skeleton never
    // reappears and the rows are present immediately, no waitFor needed.
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByText('stub-1')).toBeInTheDocument();
  });

  it('clicking a row navigates to its detail route, which fetches that id via REST', async () => {
    const { user } = renderApp();

    await user.click(await screen.findByRole('link', { name: /stub-1/i }));

    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('stub-1'));
    expect(screen.getByText('SINGLE')).toBeInTheDocument();
  });

  it('clicking a different row fetches that specific id, not a stale previous one', async () => {
    const { user } = renderApp();

    await user.click(await screen.findByRole('link', { name: /stub-2/i }));

    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('stub-2'));
  });

  it('persists only the threshold slice (honoring partialize) once the slider moves on a routed detail page', async () => {
    const { user } = renderApp();
    await user.click(await screen.findByRole('link', { name: /stub-1/i }));
    await waitFor(() => screen.getByRole('heading'));

    fireEvent.change(screen.getByLabelText('Threshold'), { target: { value: '80' } });

    // The persist middleware writes through `safeLocalStorage` on every
    // `set()` call - reading it back here (rather than asserting on the
    // in-memory store, already covered by useTaxpayerFilterStore.test.ts)
    // is what makes this an integration test of the persist middleware +
    // storage adapter wiring, not just the store's own reducer logic.
    const stored = safeLocalStorage.getItem('taxcalc-web:filters');
    expect(stored).not.toBeNull();
    const parsed: unknown = JSON.parse(stored as string);
    expect(parsed).toMatchObject({ state: { threshold: 80 } });
    // `searchText` never enters the persisted blob - a saved search would
    // otherwise silently refilter results on a later, unrelated visit.
    expect((parsed as { state: object }).state).not.toHaveProperty('searchText');
  });
});
