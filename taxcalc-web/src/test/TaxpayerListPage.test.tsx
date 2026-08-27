// src/test/TaxpayerListPage.test.tsx
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import type { MockedResponse } from '@apollo/client/testing';
import { axe } from 'jest-axe';
import { renderWithProviders } from './renderWithProviders';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';
import { LatestTaxpayersDocument, type LatestTaxpayersQuery } from '../gql/generated/hooks';
import { TaxpayerListPage } from '../pages/TaxpayerListPage';

const VARIABLES = { limit: 20 };

const HAPPY_DATA: LatestTaxpayersQuery = {
  latestTaxpayers: [
    {
      id: 'stub-1',
      tags: ['flagged'],
      lines: [{ id: 'line-1', description: 'Wages', amount: 100 }],
    },
    { id: 'stub-2', tags: [], lines: [] },
    { id: 'stub-3', tags: [], lines: [] },
  ],
};

function happyMock(): MockedResponse {
  return { request: { query: LatestTaxpayersDocument, variables: VARIABLES }, result: { data: HAPPY_DATA } };
}

function emptyMock(): MockedResponse {
  return {
    request: { query: LatestTaxpayersDocument, variables: VARIABLES },
    result: { data: { latestTaxpayers: [] } },
  };
}

function errorMock(message: string): MockedResponse {
  return {
    request: { query: LatestTaxpayersDocument, variables: VARIABLES },
    // A plain GraphQLFormattedError object, not a `GraphQLError` class
    // instance - MockedResponse's `errors` field is typed against the
    // over-the-wire formatted shape, and a class instance's `locations`
    // (`readonly [] | undefined`) doesn't satisfy that under
    // `exactOptionalPropertyTypes`.
    result: { errors: [{ message }] },
  };
}

function renderListPage(mocks: MockedResponse[]): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(<TaxpayerListPage></TaxpayerListPage>, { mocks, initialEntries: ['/taxpayers'] });
}

// useTaxpayerFilterStore is a module-level singleton (also read by
// TaxpayerDetailPage's FilterStrip) - reset before each test so a search
// typed in one test doesn't leak into the next.
beforeEach(() => {
  useTaxpayerFilterStore.getState().reset();
});

describe('TaxpayerListPage', () => {
  it('renders the page heading regardless of load state', () => {
    renderListPage([happyMock()]);
    expect(screen.getByRole('heading', { name: 'Taxpayers' })).toBeInTheDocument();
  });

  it('renders a polite role="status" skeleton while the query is in flight', () => {
    renderListPage([happyMock()]);
    expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
  });

  it('renders three rows once the mocked query resolves', async () => {
    renderListPage([happyMock()]);

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3));
    expect(screen.getByText('stub-1')).toBeInTheDocument();
  });

  it('shows the first row by accessible name once the mock resolves', async () => {
    renderListPage([happyMock()]);

    expect(await screen.findByRole('link', { name: /stub-1/i })).toBeInTheDocument();
  });

  it('links each row to its detail route', async () => {
    renderListPage([happyMock()]);

    const link = await screen.findByRole('link', { name: /stub-1/i });
    expect(link).toHaveAttribute('href', '/taxpayers/stub-1');
  });

  it('renders a taxpayer’s tags inline when it has any', async () => {
    renderListPage([happyMock()]);

    // `listitem` isn't a name-from-content role, so the row is located via
    // its link's accessible name and then walked up to the enclosing `<li>`.
    const link = await screen.findByRole('link', { name: /stub-1/i });
    const row = link.closest('li') as HTMLElement;
    expect(within(row).getByText(/flagged/i)).toBeInTheDocument();
  });

  it('renders a row with no tag suffix when the taxpayer has none', async () => {
    renderListPage([happyMock()]);

    const link = await screen.findByRole('link', { name: /stub-2/i });
    const row = link.closest('li') as HTMLElement;
    expect(within(row).queryByText(/\(/)).not.toBeInTheDocument();
  });

  it('renders a role="status" "no results" empty state when the query resolves with zero rows', async () => {
    renderListPage([emptyMock()]);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/no taxpayers yet/i));
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  });

  it('renders a role="alert" error banner when the query fails', async () => {
    renderListPage([errorMock('backend unavailable')]);

    expect(await screen.findByRole('alert')).toHaveTextContent(/backend unavailable/i);
  });

  it('names the list region for assistive tech', async () => {
    renderListPage([happyMock()]);

    expect(await screen.findByRole('list', { name: 'taxpayer-list' })).toBeInTheDocument();
  });

  it('narrows the visible rows once a search term is typed (filter-store wiring)', async () => {
    const { user } = renderListPage([happyMock()]);
    await screen.findByText('stub-1');
    expect(screen.getAllByRole('listitem')).toHaveLength(3);

    await user.type(screen.getByLabelText('Search'), 'stub-1');

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1));
    expect(screen.getByText('stub-1')).toBeInTheDocument();
    expect(screen.queryByText('stub-2')).not.toBeInTheDocument();
  });

  it('has no detectable accessibility violations once the rows have loaded', async () => {
    const { container } = renderListPage([happyMock()]);
    await screen.findByText('stub-1');

    // One scan on the loaded, happy-path state - not one per test above -
    // is the budget; the failure output names the rule and the offending
    // selector if this ever regresses.
    expect(await axe(container)).toHaveNoViolations();
  });
});
