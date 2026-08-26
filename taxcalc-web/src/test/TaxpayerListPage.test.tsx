// src/test/TaxpayerListPage.test.tsx
import { describe, it, expect } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { graphql, HttpResponse } from 'msw';
import { server } from './server';
import { renderWithProviders } from './renderWithProviders';
import { TaxpayerListPage } from '../pages/TaxpayerListPage';

function renderListPage(): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(<TaxpayerListPage></TaxpayerListPage>, { initialEntries: ['/taxpayers'] });
}

describe('TaxpayerListPage', () => {
  it('renders a polite role="status" skeleton while the query is in flight', () => {
    renderListPage();
    expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
  });

  it('renders three rows once the MSW handler resolves', async () => {
    renderListPage();

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3));
    expect(screen.getByText('stub-1')).toBeInTheDocument();
  });

  it('shows the first row by accessible name once the mock resolves', async () => {
    renderListPage();

    expect(await screen.findByRole('link', { name: /stub-1/i })).toBeInTheDocument();
  });

  it('links each row to its detail route', async () => {
    renderListPage();

    const link = await screen.findByRole('link', { name: /stub-1/i });
    expect(link).toHaveAttribute('href', '/taxpayers/stub-1');
  });

  it('renders a taxpayer’s tags inline when it has any', async () => {
    renderListPage();

    // `listitem` isn't a name-from-content role, so the row is located via
    // its link's accessible name and then walked up to the enclosing `<li>`.
    const link = await screen.findByRole('link', { name: /stub-1/i });
    const row = link.closest('li') as HTMLElement;
    expect(within(row).getByText(/flagged/i)).toBeInTheDocument();
  });

  it('renders a row with no tag suffix when the taxpayer has none', async () => {
    renderListPage();

    const link = await screen.findByRole('link', { name: /stub-2/i });
    const row = link.closest('li') as HTMLElement;
    expect(within(row).queryByText(/\(/)).not.toBeInTheDocument();
  });

  it('renders a "no results" empty state when the query resolves with zero rows', async () => {
    server.use(
      graphql.query('LatestTaxpayers', () =>
        HttpResponse.json({ data: { latestTaxpayers: [] } }),
      ),
    );
    renderListPage();

    expect(await screen.findByText(/no taxpayers yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  });

  it('renders a role="alert" error banner when the query fails', async () => {
    server.use(
      graphql.query('LatestTaxpayers', () =>
        HttpResponse.json({ errors: [{ message: 'backend unavailable' }] }),
      ),
    );
    renderListPage();

    expect(await screen.findByRole('alert')).toHaveTextContent(/backend unavailable/i);
  });

  it('names the list region for assistive tech', async () => {
    renderListPage();

    expect(await screen.findByRole('list', { name: 'taxpayer-list' })).toBeInTheDocument();
  });
});
