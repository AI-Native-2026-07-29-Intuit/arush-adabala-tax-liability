// src/test/taxpayer.a11y.test.tsx
//
// jest-axe (in TaxpayerListPage.test.tsx / TaxpayerSummaryPage.test.tsx)
// and @axe-core/playwright (in the E2E spec) both scan static DOM structure
// for WCAG rule violations - neither one drives the keyboard, so neither
// can catch a genuinely broken *tab order* (a control reachable only via
// mouse, or one that comes before something it should come after). This
// file is dedicated to that one concern: confirming a keyboard-only user
// reaches the page's interactive controls in a sensible, predictable order.
import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import type { MockedResponse } from '@apollo/client/testing';
import { renderWithProviders } from './renderWithProviders';
import { useTaxpayerFilterStore } from '../stores/useTaxpayerFilterStore';
import { LatestTaxpayersDocument, type LatestTaxpayersQuery } from '../gql/generated/hooks';
import { TaxpayerListPage } from '../pages/TaxpayerListPage';

const VARIABLES = { limit: 20 };

const HAPPY_DATA: LatestTaxpayersQuery = {
  latestTaxpayers: [
    { id: 'stub-1', tags: [], lines: [] },
    { id: 'stub-2', tags: [], lines: [] },
    { id: 'stub-3', tags: [], lines: [] },
  ],
};

function happyMock(): MockedResponse {
  return { request: { query: LatestTaxpayersDocument, variables: VARIABLES }, result: { data: HAPPY_DATA } };
}

beforeEach(() => {
  useTaxpayerFilterStore.getState().reset();
});

describe('TaxpayerListPage keyboard tab order', () => {
  it('tabs from the search input through each taxpayer row link, in document order', async () => {
    const { user } = renderWithProviders(<TaxpayerListPage></TaxpayerListPage>, {
      mocks: [happyMock()],
      initialEntries: ['/taxpayers'],
    });
    await screen.findByText('stub-1');

    const searchInput = screen.getByLabelText('Search');
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(3);

    // Nothing on this page sets a custom tabIndex, so focus order should
    // exactly match document order: the <h1> isn't focusable at all, so
    // the very first Tab press lands straight on the search input.
    await user.tab();
    expect(document.activeElement).toBe(searchInput);

    for (const link of links) {
      await user.tab();
      expect(document.activeElement).toBe(link);
    }

    // One more Tab past the last row has nothing left on this page to
    // land on - confirms the loop above didn't just get lucky on an
    // element order jsdom happened to produce.
    await user.tab();
    expect(document.activeElement).not.toBe(links[links.length - 1]);
  });

  it('typing in the search box does not change the tab order of the rows behind it', async () => {
    const { user } = renderWithProviders(<TaxpayerListPage></TaxpayerListPage>, {
      mocks: [happyMock()],
      initialEntries: ['/taxpayers'],
    });
    await screen.findByText('stub-1');

    await user.type(screen.getByLabelText('Search'), 'stub-1');
    const link = await screen.findByRole('link', { name: /stub-1/i });

    // Narrowed to one row by the search above - Tab from the (still
    // focused, mid-typing) search input should land directly on it.
    await user.tab();
    expect(document.activeElement).toBe(link);
  });
});
