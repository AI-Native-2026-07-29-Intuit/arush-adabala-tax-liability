// src/test/TaxpayerDetailPage.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { TaxpayerDetailPage } from '../pages/TaxpayerDetailPage';

const MOCK = {
  id: 'stub-id-1',
  displayName: 'Jane Doe',
  filingStatus: 'SINGLE',
  homeJurisdiction: 'COLORADO',
  createdAt: '2025-01-01T00:00:00Z',
  liabilities: [],
  tags: [],
};

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify(MOCK)))));
});

/** `useGetTaxLiabilityRest` reads `:id` via `useParams`, and the reducer's own effect needs a fresh QueryClient per test so no cached result leaks between them. */
function renderAtDetailRoute(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/taxpayers/stub-id-1']}>
        <Routes>
          <Route path="/taxpayers/:id" element={<TaxpayerDetailPage></TaxpayerDetailPage>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('TaxpayerDetailPage', () => {
  it('renders entity id and a sample field from the REST response', async () => {
    renderAtDetailRoute();
    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('stub-id-1'));
    expect(screen.getByText('SINGLE')).toBeInTheDocument();
  });

  it('updates the readout when the slider is moved (lifted state)', async () => {
    renderAtDetailRoute();
    await waitFor(() => screen.getByRole('heading'));

    const slider = screen.getByLabelText('Threshold');
    fireEvent.change(slider, { target: { value: '51' } });

    expect(screen.getByRole('status')).toHaveTextContent(/Threshold:\s*51%/);
  });

  it('renders the empty state on a 404 (useGetTaxLiabilityRest resolves null)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(null, { status: 404 }))));
    renderAtDetailRoute();
    await waitFor(() => expect(screen.getByText('Not found.')).toBeInTheDocument());
  });
});
