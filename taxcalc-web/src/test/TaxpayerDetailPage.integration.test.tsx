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
import { taxpayerRestErrorHandler, taxpayerRestLoadingHandler } from './handlers';
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
  it('shows "Loading…" before the MSW-backed REST response lands, then the real data replaces it', async () => {
    // The artificially-delayed handler (not the default near-instant one)
    // gives this a real, deliberate window to observe the loading state in,
    // rather than relying on a promise always settling a microtask later.
    server.use(taxpayerRestLoadingHandler);
    renderAtDetailRoute();

    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(await screen.findByRole('heading')).toHaveTextContent('stub-1');
  });

  it('renders the taxpayer heading and filingStatus once the REST fetch resolves', async () => {
    renderAtDetailRoute();

    expect(await screen.findByRole('heading')).toHaveTextContent('stub-1');
    expect(screen.getByText('SINGLE')).toBeInTheDocument();
  });

  it('surfaces a role="alert" with a clear error string when the REST endpoint 500s', async () => {
    server.use(taxpayerRestErrorHandler);
    renderAtDetailRoute();

    expect(await screen.findByRole('alert')).toHaveTextContent(/HTTP 500/i);
  });

  it('renders every liability as a table row/cell from the REST payload', async () => {
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

    // 1 header row (role="row" from <thead><tr>) + 2 data rows.
    const rows = await screen.findAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(await screen.findByRole('cell', { name: 'ca-bracket-0' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'ca-bracket-1' })).toBeInTheDocument();
  });

  it('renders a dash placeholder when the REST payload has no tags', async () => {
    renderAtDetailRoute();

    expect(await screen.findByText('—')).toBeInTheDocument();
  });

  it('narrows the visible liability rows once a matching search term is typed (filter-store + REST integration)', async () => {
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
    const { user } = renderAtDetailRoute();
    await screen.findByRole('cell', { name: 'ca-bracket-0' });
    expect(screen.getAllByRole('row')).toHaveLength(3);

    await user.type(screen.getByLabelText('Search'), 'bracket-0');

    // Both the cosmetic "filtering for" text and the REST-driven table rows
    // update together - the search box is wired to one store, but this
    // asserts the actual visible cells, not just the echoed search string.
    await waitFor(() => expect(screen.getByText(/filtering for: 'bracket-0'/i)).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(2));
    expect(screen.getByRole('cell', { name: 'ca-bracket-0' })).toBeInTheDocument();
    expect(screen.queryByRole('cell', { name: 'ca-bracket-1' })).not.toBeInTheDocument();
  });

  it('moving the threshold slider updates the readout while REST-driven content stays rendered', async () => {
    renderAtDetailRoute();
    expect(await screen.findByRole('heading')).toBeInTheDocument();

    // fireEvent, not userEvent, is a deliberate exception here, verified by
    // hand: jsdom implements no native layout/rendering engine, so an
    // `<input type="range">` never actually steps its value in response to
    // userEvent's synthetic keyboard or pointer events the way a real
    // browser would - confirmed by testing userEvent's own arrow-key
    // simulation against a bare range input, which left `.value` unchanged.
    // fireEvent.change, which sets the DOM value directly, is the only way
    // to drive this specific control under jsdom.
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

    expect(await screen.findByRole('heading')).toHaveTextContent('stub-1');

    await user.click(screen.getByRole('link', { name: 'Go to stub-2' }));

    expect(await screen.findByRole('heading')).toHaveTextContent('stub-2');
  });
});
