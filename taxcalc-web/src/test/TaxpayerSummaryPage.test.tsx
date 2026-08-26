// src/test/TaxpayerSummaryPage.test.tsx
import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { graphql, HttpResponse } from 'msw';
import { server } from './server';
import { renderWithProviders } from './renderWithProviders';
import { TaxpayerSummaryPage } from '../pages/TaxpayerSummaryPage';

function renderAtSummaryRoute(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/taxpayers/:id/summary" element={<TaxpayerSummaryPage></TaxpayerSummaryPage>} />
    </Routes>,
    { initialEntries: ['/taxpayers/stub-1/summary'] },
  );
}

describe('TaxpayerSummaryPage', () => {
  it('renders a "Summarize" button and no summary before it is clicked', () => {
    renderAtSummaryRoute();

    expect(screen.getByRole('button', { name: 'Summarize' })).toBeEnabled();
    expect(screen.queryByRole('definition')).not.toBeInTheDocument();
  });

  it('shows the optimistic placeholder immediately, then the server result', async () => {
    const { user } = renderAtSummaryRoute();

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    // `loading` flips true synchronously inside useMutation's execute(),
    // before the network call is even made - so the placeholder is
    // already in the DOM the instant the click resolves, with no waitFor
    // needed. filingStatus and riskBand both carry the same placeholder
    // text. The MSW handler still delays its response (see handlers.ts)
    // so the two waitFor calls below have a real window between this
    // synchronous placeholder and the real result landing.
    expect(screen.getAllByText('PENDING')).toHaveLength(2);

    await waitFor(() => expect(screen.getByText('SINGLE')).toBeInTheDocument());
    expect(screen.getByText('LOW')).toBeInTheDocument();
  });

  it('disables the button while the mutation is in flight', async () => {
    const { user } = renderAtSummaryRoute();

    const button = screen.getByRole('button', { name: 'Summarize' });
    await user.click(button);

    expect(button).toBeDisabled();
    await waitFor(() => expect(button).toBeEnabled());
  });

  it('renders totalLiability and jurisdictionCount alongside filingStatus', async () => {
    const { user } = renderAtSummaryRoute();

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    await waitFor(() => expect(screen.getByText('8420')).toBeInTheDocument());
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renders a role="alert" error banner when the mutation fails', async () => {
    server.use(
      graphql.mutation('SummarizeTaxpayer', () =>
        HttpResponse.json({ errors: [{ message: 'summary service down' }] }),
      ),
    );
    const { user } = renderAtSummaryRoute();

    await user.click(screen.getByRole('button', { name: 'Summarize' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/summary service down/i);
  });

  it('names the summary region for assistive tech', () => {
    renderAtSummaryRoute();

    expect(screen.getByRole('region', { name: 'taxpayer-summary' })).toBeInTheDocument();
  });
});
