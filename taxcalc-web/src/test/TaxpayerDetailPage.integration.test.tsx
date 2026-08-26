// src/test/TaxpayerDetailPage.integration.test.tsx
//
// Unlike TaxpayerDetailPage.test.tsx (which stubs `fetch` directly to unit
// test the reducer's branches), this file drives the page entirely through
// MSW - the REST endpoint, the Zustand filter/threshold store, and the
// debounced search hook all run for real, so these tests exercise the
// actual integration between them rather than one layer in isolation.
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { Link, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from './server';
import { taxpayerRestErrorHandler } from './handlers';
import { renderWithProviders } from './renderWithProviders';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';
import { TaxpayerDetailPage } from '../pages/TaxpayerDetailPage';

function renderAtDetailRoute(id = 'stub-1'): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/taxpayers/:id" element={<TaxpayerDetailPage></TaxpayerDetailPage>} />
    </Routes>,
    { initialEntries: [`/taxpayers/${id}`] },
  );
}

/** Zustand stores are module-level singletons - state from one test would otherwise leak into the next. */
beforeEach(() => {
  useTaxpayerFilterStore.getState().reset();
});

describe('TaxpayerDetailPage integration (MSW-backed REST + filter store)', () => {
  it('shows "Loading…" before the MSW-backed REST response lands', () => {
    renderAtDetailRoute();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('renders the taxpayer heading and filingStatus once the REST fetch resolves', async () => {
    renderAtDetailRoute();

    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('stub-1'));
    expect(screen.getByText('SINGLE')).toBeInTheDocument();
  });

  it('surfaces a role="alert" with a clear error string when the REST endpoint 500s', async () => {
    server.use(taxpayerRestErrorHandler);
    renderAtDetailRoute();

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/HTTP 500/i));
  });

  it('renders every liability line item from the REST payload', async () => {
    server.use(
      http.get('http://localhost:8080/api/v1/taxpayers/:id', ({ params }) =>
        HttpResponse.json({
          id: String(params.id),
          displayName: 'stub taxpayer',
          filingStatus: 'SINGLE',
          homeJurisdiction: 'COLORADO',
          createdAt: '2025-01-04T00:00:00Z',
          liabilities: [
            { taxYear: 2023, bracketId: 'ca-bracket-0', taxableAmount: 5000, liabilityAmount: 50, computedAt: '2025-01-04T00:00:00Z' },
            { taxYear: 2024, bracketId: 'ca-bracket-1', taxableAmount: 10000, liabilityAmount: 100, computedAt: '2025-01-04T00:00:00Z' },
          ],
          tags: [],
        }),
      ),
    );
    renderAtDetailRoute();

    const list = await screen.findByRole('list', { name: 'liabilities' });
    expect(list.children).toHaveLength(2);
  });

  it('renders a dash placeholder when the REST payload has no tags', async () => {
    renderAtDetailRoute();

    await waitFor(() => expect(screen.getByText('—')).toBeInTheDocument());
  });

  it('narrows the "filtering for" text after typing in the filter strip search box', async () => {
    const { user } = renderAtDetailRoute();
    await waitFor(() => screen.getByRole('heading'));

    await user.type(screen.getByRole('textbox', { name: 'Search' }), 'acme');

    await waitFor(() => expect(screen.getByText(/filtering for: 'acme'/i)).toBeInTheDocument());
  });

  it('moving the threshold slider updates the readout while REST-driven content stays rendered', async () => {
    renderAtDetailRoute();
    await waitFor(() => screen.getByRole('heading'));

    const slider = screen.getByLabelText('Threshold');
    fireEvent.change(slider, { target: { value: '75' } });

    expect(screen.getByRole('status')).toHaveTextContent(/Threshold:\s*75%/);
    expect(screen.getByRole('heading')).toHaveTextContent('stub-1');
  });

  it("fetches the new id's REST data when the route id changes", async () => {
    const { user } = renderWithProviders(
      <>
        <Link to="/taxpayers/stub-2">Go to stub-2</Link>
        <Routes>
          <Route path="/taxpayers/:id" element={<TaxpayerDetailPage></TaxpayerDetailPage>} />
        </Routes>
      </>,
      { initialEntries: ['/taxpayers/stub-1'] },
    );

    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('stub-1'));

    await user.click(screen.getByRole('link', { name: 'Go to stub-2' }));

    await waitFor(() => expect(screen.getByRole('heading')).toHaveTextContent('stub-2'));
  });
});
